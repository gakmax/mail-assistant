"""The 현황 screen as a NiceGUI page: the 1단계 spike of UI-PLAN.md.

nicegui is imported inside the functions that need it, so the shaping helpers below
and their tests keep working on a machine that has not installed it.
"""
import json
import os
import secrets
import socket
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .calendar_sheet import COLORS
from .core import (FAILED, HANDLED, LIST_LIMIT, PROGRESS, SORTS, STATES, Store, account_key,
                   local_text, parse_mail, row_view, state_of)
from .dashboard import CATEGORIES, PRIORITIES, describe
from .excel import mailto
from .hub import line_text
from .overview import (DUE_DAYS, RECENT_DAYS, UPCOMING, overview, past_due, results,
                       review_queue, trend)
from .settings import DEFAULTS, FIELDS, field_errors, normalize
from .style import CALM, DASH_GREEN, LINK, NEUTRAL, SOON, URGENT, css_color

SERIES = css_color(LINK)      # one hue: every count bar measures the same thing
INK = '#18181b'
MUTED = css_color(CALM)
CARD = '#fbfbfd'
LINE = '#e4e4e7'
# A status palette, not a categorical one: every bar is labelled, and 보통·낮음 are
# grey on purpose so 긴급·높음 are the only two colours competing for attention.
STATUS = {'긴급': css_color(URGENT), '높음': css_color(SOON),
          '보통': css_color(NEUTRAL), '낮음': css_color(CALM)}
# A card wears a tone only while its number is actionable. The number itself stays
# ink, so colour is never the only thing saying '이건 봐야 한다'.
CARD_TONES = {'긴급·높음': css_color(URGENT), f'{DUE_DAYS}일 내 마감': css_color(SOON),
              '검토 전 초안': SERIES}
REFRESH_SECONDS = 5.0
RUN_LINES = 8
LIVE = css_color(DASH_GREEN)
PAGES = (('/', '현황'), ('/mail', '메일'), ('/calendar', '일정'), ('/todo', '할 일'),
         ('/drafts', '초안'), ('/chat', '상담'), ('/stats', '통계'), ('/run', '실행'),
         ('/diagnose', '진단'), ('/settings', '설정'))
SORT_LABELS = (('received', '수신'), ('subject', '제목'), ('sender', '발신자'),
               ('category', '종류'), ('priority', '우선순위'), ('state', '상태'))
DEFAULT_LIST = {'query': '', 'state': '', 'sort': 'received', 'desc': True,
                'page': 0, 'per': LIST_LIMIT, 'selected': None}
WINDOWS = (7, 14, 30)           # the 마감 windows the 현황 card offers
TREND_LABELS = (('collected', '수집'), ('analyzed', '분석'), ('exported', '반영'))
TREND_TONES = {'collected': css_color(LINK), 'analyzed': css_color(NEUTRAL),
               'exported': css_color(DASH_GREEN)}
# The kanban's three columns. '' and 처리 already existed; 진행 is the new middle one.
COLUMNS = (('', '대기'), (PROGRESS, '진행'), (HANDLED, '완료'))
LOCAL = threading.local()


# ---------------------------------------------------------------- shaping (no nicegui)

def bar_rows(pairs):
    """(name, value, percent of the largest) — scaled once, so the bars stay comparable."""
    top = max((value for _, value in pairs), default=0) or 1
    return [(name, value, round(100 * value / top)) for name, value in pairs]


def deadline_rows(data, today):
    """Missed deadlines then upcoming ones, in date order: a miss must not fall off the list."""
    pairs = list(reversed(data['past_due'])) + list(data['upcoming'])
    return [{'day': day.isoformat(), 'title': entry.label, 'left': describe(day, today),
             'mail': entry.mail_id, 'missed': day < today}
            for day, entry in pairs]


def summary_line(data):
    text = (f"수집 {data['total']}건 · 분석 대기 {data['waiting']}건"
            f" · 처리 완료 {len(data['handled'])}건")
    missed = len(data['past_due'])
    return text + (f' · 지난 마감 {missed}건' if missed else '')


def run_summary(hub, directory, config, limit=RUN_LINES):
    """What the 실행 strip shows. Read-only: the hub owns the worker, not the screen."""
    state = hub.state()
    try:
        account = account_key(config)
    except (KeyError, AttributeError):
        account = ''
    stamps = {}
    if account:
        store = Store(directory / 'mail.db')
        try:
            for label, key in (('마지막 확인', 'last_fetch:'), ('마지막 반영', 'last_export:')):
                stamp = store.get_meta(key + account)
                stamps[label] = local_text(stamp, '%m-%d %H:%M:%S') if stamp else '—'
        finally:
            store.db.close()
    return {'running': state['running'], 'stopping': state['stopping'],
            'label': '중지 중…' if state['stopping'] else ('실행 중' if state['running'] else '중지됨'),
            'message': state['message'], 'stamps': stamps,
            'lines': [line_text(at, text) for at, text in hub.recent(limit)]}


def store(directory):
    """One Store per thread per folder: a connection is not shareable, and reopening
    a Store runs migrate() and both backfills again."""
    cache = getattr(LOCAL, 'stores', None)
    if cache is None:
        cache = LOCAL.stores = {}
    key = str(directory)
    if key not in cache:
        cache[key] = Store(Path(directory) / 'mail.db')
    return cache[key]


def release_store():
    for opened in getattr(LOCAL, 'stores', {}).values():
        opened.db.close()
    LOCAL.stores = {}


def account_of(config):
    try:
        return account_key(config)
    except (KeyError, AttributeError):
        return ''


def snapshot(directory, config, today=None, within=DUE_DAYS):
    account = account_of(config)
    rows = list(store(directory).page(account)) if account else []
    return overview(rows, today or date.today(), within=within)


def list_state(saved=None):
    """The list's own settings, sanitised. Anything unknown falls back to the default."""
    values = dict(DEFAULT_LIST)
    for key, value in (saved or {}).items():
        if key in values:
            values[key] = value
    if values['sort'] not in SORTS:
        values['sort'] = DEFAULT_LIST['sort']
    if values['state'] not in STATES:
        values['state'] = ''
    values['query'] = str(values['query'] or '')
    values['desc'] = bool(values['desc'])
    try:
        values['page'] = max(0, int(values['page']))
        values['per'] = min(200, max(10, int(values['per'])))
    except (TypeError, ValueError):
        values['page'], values['per'] = 0, LIST_LIMIT
    return values


def listing(directory, config, state):
    """One page of the list, plus what the pager needs to describe it."""
    account = account_of(config)
    if not account:
        return {'rows': [], 'total': 0, 'page': 0, 'pages': 0, 'first': 0, 'last': 0}
    page, per = state['page'], state['per']
    rows, total = store(directory).search(account, query=state['query'], state=state['state'],
                                          sort=state['sort'], desc=state['desc'],
                                          limit=per, offset=page * per)
    if not rows and total and page:
        # The page fell off the end, which a filter change does all the time.
        page = max(0, (total - 1) // per)
        rows, total = store(directory).search(account, query=state['query'], state=state['state'],
                                              sort=state['sort'], desc=state['desc'],
                                              limit=per, offset=page * per)
    return {'rows': [row_view(row) for row in rows], 'total': total, 'page': page,
            'pages': max(1, -(-total // per)), 'first': page * per + 1 if total else 0,
            'last': min(total, page * per + len(rows))}


def tidy_body(text):
    """HTML mail arrives as text carrying the source's indentation and runs of blank
    lines. Collapse both, or the panel opens on empty space instead of the first line.
    """
    kept = []
    for line in str(text or '').splitlines():
        line = line.strip()
        if line or (kept and kept[-1]):
            kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    return '\n'.join(kept)


def board(rows, todos):
    """{state: [card]} for the kanban. Mail-derived cards and manual ones look alike.

    The mail's own 처리 상태 is the card's column, so moving a card is the same act as
    marking the mail — there is no second source of truth to drift.
    """
    lanes = {state: [] for state, _ in COLUMNS}
    for row, result in results(rows):
        action = (result.get('next_action') or result.get('requests') or '').strip()
        if not action:
            continue
        lanes.setdefault(row['handled'] if row['handled'] in lanes else '', []).append({
            'kind': 'mail', 'key': row['id'], 'text': action,
            'note': row['subject'] or '(제목 없음)',
            'due': next((event.get('deadline', '')[:10] for event in result.get('events', [])
                         if event.get('deadline')), ''),
            'priority': result.get('priority', ''),
        })
    for todo in todos:
        state = todo['state'] if todo['state'] in lanes else ''
        lanes[state].append({'kind': 'todo', 'key': todo['id'], 'text': todo['text'],
                             'note': '직접 추가', 'due': todo['due'], 'priority': ''})
    return lanes


def draft_view(rows, chosen=None):
    """The review queue and the one being looked at."""
    queue = review_queue(rows)
    if not queue:
        return {'queue': [], 'current': None, 'index': 0}
    ids = [row['id'] for row in queue]
    position = ids.index(chosen) if chosen in ids else 0
    return {'queue': [{'id': row['id'], 'subject': row['subject'] or '(제목 없음)',
                       'sender': row['sender'], 'received': local_text(row['received'])}
                      for row in queue],
            'current': detail_view(queue[position]), 'index': position}


def chat_context(row):
    """What a chat turn may see of a mail: the analysis and a trimmed body, no raw blob."""
    if row is None:
        return None
    view = detail_view(row)
    return {'subject': view['subject'], 'sender': view['sender'], 'received': view['received'],
            'category': view['category'], 'priority': view['priority'],
            'summary': view['summary'], 'requests': view['requests'],
            'events': view['events'], 'body': view['body'][:8000]}


def stats_view(store, account, today, days=30):
    """Totals and the daily trend for the 통계 page."""
    rows = list(store.page(account)) if account else []
    data = overview(rows, today)
    return {'trend': trend(store, account, today, days=days) if account else [],
            'categories': data['categories'], 'priorities': data['priorities'],
            'total': data['total'], 'waiting': data['waiting'],
            'handled': len(data['handled']), 'days': days}


def countdown_text(last_fetch, interval, now=None):
    """'다음 확인까지 …'. Without it a quiet 3-minute interval reads as a hang (D18)."""
    if not last_fetch:
        return '첫 확인 대기'
    try:
        previous = datetime.fromisoformat(str(last_fetch))
    except (TypeError, ValueError):
        return '—'
    moment = now or datetime.now(timezone.utc)
    left = int((previous + timedelta(seconds=int(interval)) - moment).total_seconds())
    if left <= 0:
        return '확인 차례'
    if left < 60:
        return f'다음 확인까지 {left}초'
    return f'다음 확인까지 {left // 60}분 {left % 60}초'


def run_view(hub, directory, config, services=None, limit=RUN_LINES * 3):
    """Everything the 실행 page shows. Read-only: the hub owns the worker."""
    summary = run_summary(hub, directory, config, limit=limit)
    password = '확인 불가'
    if services and services.get('read_password'):
        try:
            services['read_password'](config.get('email', ''))
            password = '저장됨'
        except LookupError:
            password = '없음'
        except Exception:
            password = '확인 실패'
    account = account_of(config)
    last_fetch = store(directory).get_meta('last_fetch:' + account) if account else None
    return dict(summary, password=password,
                countdown=countdown_text(last_fetch, config.get('interval') or 180)
                if summary['running'] else '중지됨',
                blockers=list(field_errors(config).values()))


def detail_view(row):
    """Everything the detail panel shows, shaped without touching nicegui."""
    result = json.loads(row['result']) if row['result'] else {}
    try:
        parsed = json.loads(row['parsed']) if row['parsed'] else parse_mail(row['raw'])
    except Exception:
        parsed = {}
    return {
        'id': row['id'], 'subject': row['subject'] or parsed.get('subject') or '(제목 없음)',
        'sender': row['sender'] or parsed.get('sender', ''),
        'received': local_text(row['received'], '%Y-%m-%d %H:%M'),
        'category': result.get('category', ''), 'priority': result.get('priority', ''),
        'state': state_of(row), 'handled': row['handled'] == HANDLED, 'error': row['error'],
        'summary': result.get('summary', ''), 'requests': result.get('requests', ''),
        'reason': result.get('priority_reason', ''), 'action': result.get('next_action', ''),
        'events': list(result.get('events', [])),
        'attachments': list(parsed.get('attachments', [])),
        'body': tidy_body(parsed.get('body', '')),
        'draft': row['draft_edit'] or result.get('reply_draft', ''),
        'reply_subject': result.get('reply_subject', '') or f"Re: {row['subject']}",
        'analysed': bool(result),
    }


def vendor_path():
    """Where the bundled FullCalendar lives, frozen or not."""
    return Path(__file__).resolve().parent / 'vendor'


def calendar_events(events, today, handled=()):
    """calendar_sheet entries as FullCalendar events — all of them.

    A mailbox produces hundreds, not thousands, so handing the client the whole set
    lets it page through months without another round trip.
    """
    payload = []
    for day in sorted(events):
        for entry in events[day]:
            if entry.mail_id in handled:
                continue
            payload.append({
                'id': entry.mail_id,
                'title': entry.label,
                'start': day.isoformat(),
                'allDay': True,
                'color': css_color(COLORS[entry.kind]),
                'extendedProps': {'kind': entry.kind, 'evidence': entry.evidence,
                                  'missed': entry.kind == '마감' and day < today},
            })
    return payload


def href(path, token, **params):
    """Every internal link carries the token: it is what gates the data."""
    pairs = [('t', token)] + [(key, value) for key, value in params.items() if value]
    return path + '?' + '&'.join(f'{key}={value}' for key, value in pairs)


def new_token():
    return secrets.token_urlsafe(16)


def open_port(host='127.0.0.1'):
    """A free loopback port, so a second install or another app cannot collide with us."""
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------- page

def section(title):
    from nicegui import ui
    ui.label(title).style(f'color:{INK};font-size:14px;font-weight:700;margin:14px 0 6px')


def card_target(name, token, within=DUE_DAYS):
    """Where a card sends you. A number you cannot act on is only decoration (D2)."""
    if name == '긴급·높음':
        return href('/mail', token, state='미처리', sort='priority')
    if name == '미처리 메일':
        return href('/mail', token, state='미처리')
    if name == f'{within}일 내 마감':
        return href('/calendar', token)
    if name == '검토 전 초안':
        return href('/drafts', token)
    return None


def cards(data, token):
    from nicegui import ui
    with ui.element('div').style('display:grid;gap:10px;'
                                 'grid-template-columns:repeat(auto-fit,minmax(150px,1fr))'):
        for name, value in data['cards'].items():
            tone = CARD_TONES.get(name) if value else None
            target = card_target(name, token, data.get('within', DUE_DAYS))
            box = ui.link(target=target) if target else ui.element('div')
            box.style(f'background:{CARD};border:1px solid {LINE};border-radius:8px;'
                      'padding:12px 14px;text-decoration:none;display:block'
                      + (f';border-left:3px solid {tone}' if tone else ''))
            with box:
                ui.label(name).style(f'color:{MUTED};font-size:12px')
                ui.label(str(value)).style(f'color:{INK};font-size:26px;font-weight:700;'
                                           'line-height:1.25')


def label_step(count, shown=10):
    """Print every nth date: 90 day labels in a row is a grey smear."""
    return max(1, -(-count // shown))


def trend_block(rows):
    """Collected, analysed and exported per day — Store.day_counts() finally on screen."""
    from nicegui import ui
    section('일별 처리량')
    top = max((counts[key] for _, counts in rows for key, _ in TREND_LABELS), default=0) or 1
    step = label_step(len(rows))
    # Flexible widths, not fixed: at 90 days a 10px bar per measure overflows the row.
    with ui.element('div').style('display:flex;align-items:flex-end;gap:3px;width:100%'):
        for index, (day, counts) in enumerate(rows):
            with ui.element('div').style('flex:1;min-width:0;display:flex;'
                                         'flex-direction:column;align-items:center;'
                                         'justify-content:flex-end'):
                with ui.element('div').style('display:flex;align-items:flex-end;gap:1px;'
                                             'height:80px;width:100%'):
                    for key, label in TREND_LABELS:
                        value = counts[key]
                        share = round(100 * value / top) if value else 0
                        ui.element('div').style(
                            f'flex:1;min-width:2px;max-width:10px;'
                            f'height:{max(share, 2) if value else 0}%;'
                            f'border-radius:2px 2px 0 0;background:{TREND_TONES[key]}')
                        ui.tooltip(f'{day} {label} {value}건')
                ui.label(day[5:] if index % step == 0 else '') \
                    .style(f'color:{MUTED};font-size:10px;margin-top:4px;white-space:nowrap')
    with ui.element('div').style('display:flex;gap:10px;margin-top:4px'):
        for key, label in TREND_LABELS:
            with ui.element('div').style('display:flex;align-items:center;gap:4px'):
                ui.label('■').style(f'color:{TREND_TONES[key]};font-size:10px')
                ui.label(label).style(f'color:{MUTED};font-size:11px')


def bar_list(pairs, tones=None):
    """Labelled horizontal bars: identity comes from the label, length from the count."""
    from nicegui import ui
    with ui.element('div').style('display:flex;flex-direction:column;gap:2px'):
        for name, value, percent in bar_rows(pairs):
            with ui.element('div').style('display:grid;gap:8px;align-items:center;'
                                         'grid-template-columns:minmax(56px,84px) 34px 1fr'):
                ui.label(name).style(f'color:{INK};font-size:13px')
                ui.label(str(value)).style(f'font-size:13px;font-weight:600;text-align:right;'
                                           f"color:{INK if value else MUTED}")
                with ui.element('div').style(f'height:10px;border-radius:5px;background:{LINE};'
                                             'overflow:hidden;max-width:320px'):
                    ui.element('div').style(
                        f'width:{percent}%;height:100%;border-radius:0 5px 5px 0;'
                        f"background:{(tones or {}).get(name, SERIES)}")
                    ui.tooltip(f'{name} {value}건')


def column_list(pairs):
    """Seven days read left to right, so time keeps its own axis."""
    from nicegui import ui
    with ui.element('div').style('display:flex;align-items:flex-end;gap:6px;width:100%'):
        for name, value, percent in bar_rows(pairs):
            with ui.element('div').style('flex:1;display:flex;flex-direction:column;'
                                         'align-items:center;justify-content:flex-end;'
                                         'height:116px'):
                ui.label(str(value)).style(f'font-size:12px;font-weight:600;margin-bottom:2px;'
                                           f"color:{INK if value else MUTED}")
                ui.element('div').style(f'width:100%;background:{SERIES};'
                                        'border-radius:5px 5px 0 0;'
                                        f'height:{max(percent, 2) if value else 0}%')
                ui.tooltip(f'{name} {value}건')
                ui.label(name).style(f'color:{MUTED};font-size:11px;margin-top:4px')


def deadlines(data, today, on_open):
    from nicegui import ui
    columns = [{'name': 'day', 'label': '마감', 'field': 'day', 'align': 'left'},
               {'name': 'title', 'label': '일정', 'field': 'title', 'align': 'left'},
               {'name': 'left', 'label': '남음', 'field': 'left', 'align': 'left'}]
    rows = deadline_rows(data, today)
    if not rows:
        ui.label('마감이 있는 일정이 없습니다.').style(f'color:{MUTED};font-size:13px')
        return
    table = ui.table(columns=columns, rows=rows, row_key='title').classes('w-full')
    table.props('flat dense')
    # The text already says 지남; the colour only makes it findable at a glance.
    table.add_slot('body-cell-left', '''
        <q-td :props="props">
          <span :style="props.row.missed ? 'color:''' + css_color(URGENT) + ''';font-weight:600' : ''">
            {{ props.value }}
          </span>
        </q-td>''')
    table.on('rowClick', lambda event: on_open(event.args[1]['mail']))


def run_strip(summary):
    from nicegui import ui
    section('실행 상태')
    with ui.element('div').style('display:flex;align-items:center;gap:8px;flex-wrap:wrap'):
        ui.label('●').style(f"color:{LIVE if summary['running'] else MUTED};font-size:13px")
        ui.label(summary['label']).style(f'color:{INK};font-size:13px;font-weight:600')
        for label, value in summary['stamps'].items():
            ui.label(f'{label} {value}').style(f'color:{MUTED};font-size:12px')
    if summary['message']:
        ui.label(summary['message']).style(f'color:{INK};font-size:12px;margin-top:6px')
    if summary['lines']:
        with ui.element('div').style(f'display:block;margin-top:8px;padding:8px 10px;'
                                     f'background:{CARD};border:1px solid {LINE};'
                                     'border-radius:8px;font-size:11px;line-height:1.7;'
                                     'max-height:180px;overflow-y:auto;width:100%'):
            for text in summary['lines']:
                ui.label(text).style(f'color:{MUTED};white-space:pre-wrap;'
                                     'overflow-wrap:anywhere')


def shell(current, token):
    """Header and navigation, identical on every page."""
    from contextlib import contextmanager
    from nicegui import ui

    @contextmanager
    def frame():
        ui.add_head_html("<style>body{font-family:'맑은 고딕','Malgun Gothic',"
                         'system-ui,sans-serif}</style>')
        with ui.element('div').style('width:100%;max-width:1180px;margin:0 auto;'
                                     'padding:18px 16px') as page:
            with ui.element('div').style('display:flex;align-items:baseline;gap:14px;'
                                         'flex-wrap:wrap;margin-bottom:14px'):
                ui.label('메일 업무 도우미').style(f'color:{INK};font-size:20px;font-weight:700')
                ui.label(__version__).style(f'color:{MUTED};font-size:12px')
                for path, name in PAGES:
                    live = path == current
                    ui.link(name, href(path, token)).style(
                        f"color:{INK if live else MUTED};font-size:13px;text-decoration:none;"
                        f"font-weight:{'700' if live else '400'};"
                        f"border-bottom:2px solid {SERIES if live else 'transparent'};"
                        'padding-bottom:2px')
            yield page
    return frame()


def refused():
    from nicegui import ui
    ui.label('이 주소로는 열 수 없습니다. 메일 도우미가 띄운 창에서 다시 열어 주세요.') \
        .style(f'color:{INK};padding:24px')


def chips(pairs):
    """Small grey facts in a row: sender, time, category, state."""
    from nicegui import ui
    with ui.element('div').style('display:flex;gap:10px;flex-wrap:wrap;margin:2px 0 10px'):
        for text, tone in pairs:
            if text:
                ui.label(text).style(f'color:{tone or MUTED};font-size:12px')


# In the head, without async/defer: a body script loads while run_javascript is
# already firing, and the initialiser then finds window.FullCalendar undefined.
CALENDAR_SCRIPT = '<script src="/vendor/fullcalendar/index.global.min.js"></script>'
CALENDAR_SETUP = """
<script>
// Korean strings are set here rather than by loading a locale bundle: FullCalendar 6
// ships locales as a separate package and this is the whole of what we need.
window.mailCalendar = function (events, tries) {
  const host = document.getElementById('calendar');
  // Vue may not have mounted the div yet, so wait rather than silently doing nothing.
  if (!host || !window.FullCalendar) {
    if ((tries || 0) < 100) {
      setTimeout(() => window.mailCalendar(events, (tries || 0) + 1), 50);
      return;
    }
    // A missing vendor file must say so instead of leaving an empty page.
    if (host) {
      host.dataset.state = 'no-library';
      host.textContent = '달력 구성 요소를 불러오지 못했습니다. 설치가 손상되었을 수 있습니다.';
    }
    return;
  }
  host.dataset.state = 'ready';
  const weekdays = ['일', '월', '화', '수', '목', '금', '토'];
  const stamp = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
                       + `-${String(d.getDate()).padStart(2, '0')}`;
  host.innerHTML = '';
  const calendar = new FullCalendar.Calendar(host, {
    initialView: 'dayGridMonth',
    firstDay: 1,
    height: 'auto',
    expandRows: true,
    dayMaxEvents: 4,
    events: events,
    headerToolbar: {left: 'prev,next today', center: '',
                    right: 'dayGridMonth,timeGridWeek,listMonth'},
    buttonText: {today: '오늘', month: '월', week: '주', list: '목록'},
    allDayText: '종일',
    noEventsText: '이 기간에는 일정이 없습니다.',
    moreLinkText: (n) => `외 ${n}건`,
    dayHeaderContent: (arg) => weekdays[arg.date.getDay()],
    datesSet: (info) => {
      const label = document.getElementById('calendar-title');
      if (!label) return;
      const start = info.view.currentStart;
      const last = new Date(info.view.currentEnd.getTime() - 86400000);
      label.textContent = info.view.type === 'dayGridMonth'
        ? `${start.getFullYear()}년 ${start.getMonth() + 1}월`
        : `${stamp(start)} ~ ${stamp(last)}`;
    },
    eventDidMount: (info) => {
      const note = info.event.extendedProps.evidence;
      info.el.title = note ? `${info.event.title}\n근거: ${note}` : info.event.title;
      if (info.event.extendedProps.missed) info.el.style.textDecoration = 'line-through';
    },
    eventClick: (info) => { info.jsEvent.preventDefault(); emitEvent('mail-open', info.event.id); },
  });
  calendar.render();
};
</script>
"""


def build(directory, config, token, hub=None, services=None, config_path=None,
          can_start=True):
    """Register the pages. The token gates the data, not the static assets."""
    import subprocess

    from fastapi import Request
    from nicegui import app, run as nicerun, ui

    from . import services as helpers, update
    from .report import remember_secret

    config_path = config_path or (directory / 'config.json')

    # Served from the package, never from a CDN: this app has to work offline.
    app.add_static_files('/vendor', str(vendor_path()))

    def allowed(request):
        return not token or request.query_params.get('t') == token

    # 현황 -------------------------------------------------------------

    @ui.page('/')
    def home(request: Request):
        if not allowed(request):
            refused()
            return

        window = {'days': DUE_DAYS}

        @ui.refreshable
        def body():
            data = snapshot(directory, config, within=window['days'])
            today = date.today()
            cards(data, token)
            with ui.element('div').style('display:grid;gap:24px;margin-top:6px;'
                                         'grid-template-columns:repeat(auto-fit,minmax(300px,1fr))'):
                with ui.element('div'):
                    section('메일 종류')
                    bar_list([(name, data['categories'][name]) for name in CATEGORIES])
                    section('우선순위')
                    bar_list([(name, data['priorities'][name]) for name, _ in PRIORITIES], STATUS)
                    section('최근 7일 수신')
                    column_list([(day[5:], count) for day, count in data['recent']])
                    trend_block(trend(store(directory), account_of(config), today))
                with ui.element('div'):
                    with ui.element('div').style('display:flex;align-items:baseline;gap:8px;'
                                                 'margin:14px 0 6px'):
                        ui.label('마감 임박 · 지난 마감') \
                            .style(f'color:{INK};font-size:14px;font-weight:700')
                        for days in WINDOWS:
                            live = days == window['days']
                            ui.button(f'{days}일', on_click=lambda d=days: pick(d)) \
                                .props('flat dense no-caps' + ('' if live else ' text-color=grey'))
                    deadlines(data, today,
                              lambda ident: ui.navigate.to(href('/mail', token, id=ident)))
                    if hub is not None:
                        run_strip(run_summary(hub, directory, config))
            ui.label(summary_line(data)).style(f'color:{MUTED};font-size:12px;margin-top:14px')

        def pick(days):
            window['days'] = days
            body.refresh()

        with shell('/', token):
            body()
            ui.timer(REFRESH_SECONDS, body.refresh)

    # 메일 -------------------------------------------------------------

    @ui.page('/mail')
    def mail(request: Request):
        if not allowed(request):
            refused()
            return
        saved = dict(app.storage.user.get('list') or {})
        # A card link arrives with its own filter; it wins over what was remembered.
        for key in ('state', 'sort', 'query'):
            if key in request.query_params:
                saved[key] = request.query_params[key]
                saved['page'] = 0
        state = list_state(saved)
        opened = request.query_params.get('id')
        if opened:
            state['selected'] = opened

        def remember(**changes):
            state.update(changes)
            app.storage.user['list'] = dict(state)

        def choose(ident):
            remember(selected=ident)
            panel.refresh()

        def edit(**changes):
            remember(page=0, **changes)
            rows.refresh()

        @ui.refreshable
        def rows():
            data = listing(directory, config, state)
            if data['page'] != state['page']:
                remember(page=data['page'])
            # Widths are fixed except 제목, which takes the rest; max-width:0 is the
            # trick that makes a flexible table cell ellipsize instead of overflowing.
            widths = {'received': 'width:96px', 'sender': 'width:190px',
                      'subject': 'max-width:0;overflow:hidden;text-overflow:ellipsis;'
                                 'white-space:nowrap',
                      'category': 'width:84px', 'priority': 'width:78px', 'state': 'width:84px'}
            columns = [{'name': key, 'label': label, 'field': key, 'align': 'left',
                        'style': widths[key], 'headerStyle': widths[key].split(';')[0]}
                       for key, label in (('received', '수신'), ('sender', '발신자'),
                                          ('subject', '제목'), ('category', '종류'),
                                          ('priority', '우선순위'), ('state', '상태'))]
            with ui.element('div').style('width:100%;overflow-x:auto'):
                table = ui.table(columns=columns, rows=data['rows'], row_key='id',
                                 selection='multiple').classes('w-full')
                table.props('flat dense wrap-cells=false')
                # Colour never carries the fact alone: the cell text already says it.
                table.add_slot('body-cell-state', """
                    <q-td :props="props">
                      <span :style="props.value.includes('실패') ? 'color:%s;font-weight:600'
                                  : (props.value === '%s' ? 'color:%s' : '')">{{ props.value }}</span>
                    </q-td>""" % (css_color(SOON), HANDLED, MUTED))
                table.add_slot('body-cell-priority', """
                    <q-td :props="props">
                      <span :style="props.value === '긴급' ? 'color:%s;font-weight:600' : ''">
                        {{ props.value }}</span>
                    </q-td>""" % css_color(URGENT))
                table.on('rowClick', lambda event: choose(event.args[1]['id']))

            def picked():
                return [row['id'] for row in table.selected]

            def mark(value):
                ids = picked()
                if not ids:
                    ui.notify('선택된 메일이 없습니다.')
                    return
                store(directory).set_handled_many(ids, value)
                table.selected.clear()
                rows.refresh()
                panel.refresh()
                ui.notify(f"{len(ids)}건을 {'처리 완료' if value else '미처리'}로 바꿨습니다.")

            def again():
                ids = picked()
                if not ids:
                    ui.notify('선택된 메일이 없습니다.')
                    return
                store(directory).reset(ids, reanalyze=True)
                if hub is not None:
                    hub.wake()
                table.selected.clear()
                rows.refresh()
                panel.refresh()
                ui.notify(f'{len(ids)}건을 다시 분석하도록 요청했습니다.')

            def step(delta):
                remember(page=max(0, min(data['pages'] - 1, state['page'] + delta)))
                rows.refresh()

            with ui.element('div').style('display:flex;gap:8px;align-items:center;'
                                         'flex-wrap:wrap;margin-top:10px'):
                ui.label(f"{data['first']}–{data['last']} / {data['total']}건" if data['total']
                         else '조건에 맞는 메일이 없습니다.').style(f'color:{MUTED};font-size:12px')
                ui.button('처리 완료', on_click=lambda: mark(HANDLED)).props('flat dense no-caps')
                ui.button('미처리로', on_click=lambda: mark('')).props('flat dense no-caps')
                ui.button('다시 분석', on_click=again).props('flat dense no-caps')
                ui.space()
                ui.button('◀', on_click=lambda: step(-1)).props('flat dense') \
                    .set_enabled(data['page'] > 0)
                ui.label(f"{data['page'] + 1} / {data['pages']}") \
                    .style(f'color:{MUTED};font-size:12px')
                ui.button('▶', on_click=lambda: step(1)).props('flat dense') \
                    .set_enabled(data['page'] + 1 < data['pages'])

        @ui.refreshable
        def panel():
            ident = state['selected']
            row = store(directory).detail(ident) if ident else None
            if row is None:
                ui.label('목록에서 메일을 선택하세요.').style(f'color:{MUTED};font-size:13px')
                return
            view = detail_view(row)
            ui.label(view['subject']).style(f'color:{INK};font-size:16px;font-weight:700')
            chips([(view['sender'], None), (view['received'], None), (view['category'], None),
                   (view['priority'], css_color(URGENT) if view['priority'] == '긴급' else None),
                   (view['state'], None)])
            if view['error']:
                ui.label(view['error']).style(f"color:{css_color(URGENT)};font-size:12px")
            with ui.element('div').style('display:grid;gap:20px;align-items:start;'
                                         'grid-template-columns:repeat(auto-fit,minmax(320px,1fr))'):
                with ui.element('div'):
                    section('분석 결과')
                    if not view['analysed']:
                        ui.label('아직 분석되지 않았습니다.').style(f'color:{MUTED};font-size:13px')
                    for label, text in (('요약', view['summary']), ('요청사항', view['requests']),
                                        ('우선순위 근거', view['reason']), ('다음 행동', view['action'])):
                        if text:
                            ui.label(label).style(f'color:{MUTED};font-size:11px;margin-top:6px')
                            ui.label(text).style(f'color:{INK};font-size:13px;white-space:pre-wrap')
                    if view['events']:
                        ui.label('일정').style(f'color:{MUTED};font-size:11px;margin-top:8px')
                        for event in view['events']:
                            mark = ' (확인 필요)' if event.get('needs_review') else ''
                            ui.label(f"{event.get('title', '')} · 시작 {event.get('start') or '—'}"
                                     f" · 마감 {event.get('deadline') or '—'}{mark}") \
                                .style(f'color:{INK};font-size:13px')
                            if event.get('evidence'):
                                ui.label(f"근거: {event['evidence']}") \
                                    .style(f'color:{MUTED};font-size:11px;white-space:pre-wrap')
                    if view['attachments']:
                        ui.label('첨부').style(f'color:{MUTED};font-size:11px;margin-top:8px')
                        ui.label(', '.join(view['attachments'])) \
                            .style(f'color:{INK};font-size:13px')
                with ui.element('div'):
                    section('원문')
                    # display:block on purpose: a bare div inherits a centring flex
                    # layout here, which parks a short body in the middle of the box.
                    with ui.element('div').style(f'display:block;background:{CARD};'
                                                 f'border:1px solid {LINE};border-radius:8px;'
                                                 'padding:10px 12px;max-height:320px;'
                                                 'overflow-y:auto;width:100%'):
                        ui.label(view['body'] or '(본문 없음)') \
                            .style(f'display:block;color:{INK};font-size:12px;'
                                   'white-space:pre-wrap;text-align:left;'
                                   'overflow-wrap:anywhere')
            section('답변 초안')
            ui.label('입력을 멈추면 자동 저장됩니다.').style(f'color:{MUTED};font-size:11px')
            draft = ui.textarea(value=view['draft']).classes('w-full')
            draft.props('outlined autogrow debounce=800')
            draft.on_value_change(lambda event: store(directory).set_draft(view['id'],
                                                                          event.value or ''))

            async def copy():
                await ui.clipboard.write(draft.value or '')
                ui.notify('답변 초안을 클립보드에 복사했습니다.')

            def toggle():
                store(directory).set_handled(view['id'], '' if view['handled'] else HANDLED)
                rows.refresh()
                panel.refresh()

            def retry():
                store(directory).reset([view['id']], reanalyze=view['analysed'])
                if hub is not None:
                    hub.wake()
                rows.refresh()
                panel.refresh()
                ui.notify('다시 분석하도록 요청했습니다.')

            with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap;margin-top:10px'):
                ui.button('미처리로 되돌리기' if view['handled'] else '처리 완료로 표시',
                          on_click=toggle).props('flat dense no-caps')
                ui.button('초안 복사', on_click=copy).props('flat dense no-caps')
                link = mailto(view['sender'], view['reply_subject'], view['draft'])
                if link:
                    ui.link('답장 열기', link).style(f'color:{SERIES};font-size:13px;'
                                                  'align-self:center')
                ui.button('다시 분석', on_click=retry).props('flat dense no-caps')
                ui.link('이 메일로 상담', href('/chat', token, id=view['id'])) \
                    .style(f'color:{SERIES};font-size:13px;align-self:center;'
                           'text-decoration:none')

        with shell('/mail', token):
            with ui.element('div').style('display:flex;gap:8px;align-items:center;'
                                         'flex-wrap:wrap;margin-bottom:6px'):
                search = ui.input(placeholder='제목·발신자·본문·요약 검색', value=state['query'])
                search.props('dense outlined clearable debounce=400').style('min-width:260px')
                search.on_value_change(lambda event: edit(query=event.value or ''))
                picker = ui.select({value: value or '전체 상태' for value in STATES},
                                   value=state['state'])
                picker.props('dense outlined options-dense').style('min-width:130px')
                picker.on_value_change(lambda event: edit(state=event.value or ''))
                sorter = ui.select(dict(SORT_LABELS), value=state['sort'])
                sorter.props('dense outlined options-dense').style('min-width:120px')
                sorter.on_value_change(lambda event: edit(sort=event.value))
                ui.button('내림차순' if state['desc'] else '오름차순',
                          on_click=lambda: edit(desc=not state['desc'])) \
                    .props('flat dense no-caps')
            rows()
            with ui.element('div').style(f'margin-top:18px;padding-top:14px;'
                                         f'border-top:1px solid {LINE}'):
                panel()


    # 일정 -------------------------------------------------------------

    @ui.page('/calendar')
    def calendar(request: Request):
        if not allowed(request):
            refused()
            return
        ui.on('mail-open', lambda event: ui.navigate.to(href('/mail', token, id=event.args)))
        data = snapshot(directory, config)
        payload = calendar_events(data['events'], date.today(), data['handled'])
        ui.add_head_html(CALENDAR_SCRIPT)
        # The payload rides in the page rather than arriving by run_javascript: that
        # call depends on the client being connected, and a calendar that renders on
        # load has nothing to gain from the round trip.
        ui.add_body_html(CALENDAR_SETUP + '<script>window.mailCalendar('
                         + json.dumps(payload, ensure_ascii=False) + ');</script>')
        with shell('/calendar', token):
            with ui.element('div').style('display:flex;align-items:baseline;gap:14px;'
                                         'flex-wrap:wrap;margin-bottom:8px'):
                ui.label('').props('id=calendar-title') \
                    .style(f'color:{INK};font-size:15px;font-weight:700')
                for kind in ('마감', '시작', '확인 필요'):
                    with ui.element('div').style('display:flex;align-items:center;gap:4px'):
                        ui.label('■').style(f'color:{css_color(COLORS[kind])};font-size:11px')
                        ui.label(kind).style(f'color:{MUTED};font-size:12px')
                ui.label('일정을 누르면 그 메일이 열립니다.') \
                    .style(f'color:{MUTED};font-size:12px')
            ui.element('div').props('id=calendar').style('width:100%')
            missed = past_due(data['events'], date.today(), handled=data['handled'],
                              limit=UPCOMING * 3)
            if missed:
                section(f'지난 마감 {len(missed)}건')
                with ui.element('div').style('display:flex;flex-direction:column;gap:2px'):
                    for day, entry in reversed(missed):
                        with ui.element('div').style('display:flex;gap:10px;align-items:baseline'):
                            ui.label(day.isoformat()).style(f'color:{MUTED};font-size:12px')
                            ui.link(entry.label, href('/mail', token, id=entry.mail_id)) \
                                .style(f"color:{css_color(URGENT)};font-size:13px;"
                                       'text-decoration:none')
                            ui.label(describe(day, date.today())) \
                                .style(f'color:{MUTED};font-size:12px')


    # 할 일 -----------------------------------------------------------

    @ui.page('/todo')
    def todo_page(request: Request):
        if not allowed(request):
            refused()
            return
        account = account_of(config)

        def move(card, state):
            if card['kind'] == 'mail':
                store(directory).set_handled(card['key'], state)
            else:
                store(directory).set_todo_state(card['key'], state)
            lanes.refresh()

        def drop(card):
            store(directory).delete_todo(card['key'])
            lanes.refresh()

        def add():
            text = (entry.value or '').strip()
            if not text:
                ui.notify('할 일을 입력하세요.')
                return
            if not account:
                ui.notify('설정을 먼저 저장하세요.')
                return
            store(directory).add_todo(account, text, (due.value or '').strip())
            entry.set_value('')
            due.set_value('')
            lanes.refresh()

        @ui.refreshable
        def lanes():
            rows = list(store(directory).page(account)) if account else []
            todos = list(store(directory).todos(account)) if account else []
            data = board(rows, todos)
            with ui.element('div').style('display:grid;gap:14px;align-items:start;'
                                         'grid-template-columns:repeat(auto-fit,minmax(280px,1fr))'):
                for index, (state, label) in enumerate(COLUMNS):
                    cards_here = data[state]
                    with ui.element('div').style(f'background:{CARD};border:1px solid {LINE};'
                                                 'border-radius:8px;padding:10px 12px'):
                        ui.label(f'{label} {len(cards_here)}') \
                            .style(f'color:{INK};font-size:13px;font-weight:700')
                        if not cards_here:
                            ui.label('비어 있습니다.').style(f'color:{MUTED};font-size:12px')
                        for card in cards_here:
                            with ui.element('div').style('display:block;background:#ffffff;'
                                                         f'border:1px solid {LINE};'
                                                         'border-radius:6px;padding:8px 10px;'
                                                         'margin-top:6px;width:100%'):
                                ui.label(card['text']).style(f'color:{INK};font-size:13px;'
                                                             'white-space:pre-wrap')
                                with ui.element('div').style('display:flex;gap:8px;'
                                                             'flex-wrap:wrap;margin-top:2px'):
                                    ui.label(card['note']).style(f'color:{MUTED};font-size:11px')
                                    if card['due']:
                                        ui.label(f"마감 {card['due']}") \
                                            .style(f'color:{MUTED};font-size:11px')
                                    if card['priority'] == '긴급':
                                        ui.label('긴급').style(
                                            f"color:{css_color(URGENT)};font-size:11px;"
                                            'font-weight:600')
                                with ui.element('div').style('display:flex;gap:4px;margin-top:4px'):
                                    if index:
                                        ui.button('◀', on_click=lambda c=card, s=COLUMNS[index - 1][0]:
                                                  move(c, s)).props('flat dense')
                                    if index + 1 < len(COLUMNS):
                                        ui.button('▶', on_click=lambda c=card, s=COLUMNS[index + 1][0]:
                                                  move(c, s)).props('flat dense')
                                    if card['kind'] == 'mail':
                                        ui.link('메일', href('/mail', token, id=card['key'])) \
                                            .style(f'color:{SERIES};font-size:12px;'
                                                   'align-self:center;text-decoration:none')
                                    else:
                                        ui.button('삭제', on_click=lambda c=card: drop(c)) \
                                            .props('flat dense no-caps')

        with shell('/todo', token):
            ui.label('메일에서 나온 다음 행동과, 직접 적은 할 일을 한 판에 둡니다. '
                     '메일 카드를 옮기면 그 메일의 처리 상태가 함께 바뀝니다.') \
                .style(f'color:{MUTED};font-size:12px;margin-bottom:10px')
            with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px'):
                entry = ui.input(placeholder='할 일 추가').props('dense outlined') \
                    .style('min-width:260px')
                due = ui.input(placeholder='마감 (2026-09-30, 선택)').props('dense outlined') \
                    .style('min-width:190px')
                ui.button('추가', on_click=add).props('unelevated dense no-caps')
            lanes()

    # 초안 -----------------------------------------------------------

    @ui.page('/drafts')
    def drafts_page(request: Request):
        if not allowed(request):
            refused()
            return
        account = account_of(config)
        chosen = {'id': request.query_params.get('id')}

        @ui.refreshable
        def queue():
            rows = list(store(directory).unedited_drafts(account)) if account else []
            data = draft_view(rows, chosen['id'])
            if not data['queue']:
                ui.label('검토할 초안이 없습니다. 답변이 필요한 메일이 분석되면 여기에 모입니다.') \
                    .style(f'color:{MUTED};font-size:13px')
                return
            view = data['current']
            ui.label(f"{data['index'] + 1} / {len(data['queue'])}건") \
                .style(f'color:{MUTED};font-size:12px')
            ui.label(view['subject']).style(f'color:{INK};font-size:16px;font-weight:700')
            chips([(view['sender'], None), (view['received'], None), (view['category'], None),
                   (view['priority'], css_color(URGENT) if view['priority'] == '긴급' else None)])
            if view['requests']:
                ui.label('요청사항').style(f'color:{MUTED};font-size:11px')
                ui.label(view['requests']).style(f'color:{INK};font-size:13px;'
                                                 'white-space:pre-wrap')
            section('생성된 초안')
            draft = ui.textarea(value=view['draft']).classes('w-full')
            draft.props('outlined autogrow debounce=800')
            draft.on_value_change(lambda event: store(directory).set_draft(view['id'],
                                                                          event.value or ''))

            def step(delta):
                ids = [item['id'] for item in data['queue']]
                chosen['id'] = ids[(data['index'] + delta) % len(ids)]
                queue.refresh()

            async def copy():
                await ui.clipboard.write(draft.value or '')
                ui.notify('초안을 복사했습니다.')

            def done():
                store(directory).set_handled(view['id'], HANDLED)
                chosen['id'] = None
                queue.refresh()

            with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap;margin-top:10px'):
                ui.button('이전', on_click=lambda: step(-1)).props('flat dense no-caps')
                ui.button('다음', on_click=lambda: step(1)).props('flat dense no-caps')
                ui.button('초안 복사', on_click=copy).props('flat dense no-caps')
                link = mailto(view['sender'], view['reply_subject'], view['draft'])
                if link:
                    ui.link('답장 열기', link).style(f'color:{SERIES};font-size:13px;'
                                                  'align-self:center')
                ui.button('처리 완료', on_click=done).props('unelevated dense no-caps')
                ui.link('메일에서 보기', href('/mail', token, id=view['id'])) \
                    .style(f'color:{MUTED};font-size:12px;align-self:center')

        with shell('/drafts', token):
            ui.label('답변이 필요한데 아직 손대지 않은 초안입니다. 입력을 멈추면 자동 저장되고, '
                     '저장하면 검토 완료로 간주해 목록에서 빠집니다.') \
                .style(f'color:{MUTED};font-size:12px;margin-bottom:10px')
            queue()

    # 진단 -----------------------------------------------------------

    @ui.page('/diagnose')
    def diagnose_page(request: Request):
        if not allowed(request):
            refused()
            return
        found = {'steps': [], 'codex': None}

        @ui.refreshable
        def report_block():
            if not found['steps'] and not found['codex']:
                ui.label('위 버튼으로 점검을 실행하세요.').style(f'color:{MUTED};font-size:12px')
                return
            for label, ok, detail in found['steps']:
                with ui.element('div').style('display:flex;gap:8px;align-items:baseline'):
                    ui.label('OK' if ok else '실패').style(
                        f"color:{LIVE if ok else css_color(URGENT)};font-size:12px;"
                        'font-weight:700;min-width:34px')
                    ui.label(label).style(f'color:{INK};font-size:13px;min-width:110px')
                    ui.label(detail).style(f'color:{MUTED};font-size:12px;'
                                           'overflow-wrap:anywhere')
            if found['codex']:
                state, detail = found['codex']
                with ui.element('div').style('display:flex;gap:8px;align-items:baseline;'
                                             'margin-top:6px'):
                    ui.label('Codex').style(f'color:{INK};font-size:13px;min-width:110px')
                    ui.label(f'{state} — {detail}').style(f'color:{MUTED};font-size:12px;'
                                                          'overflow-wrap:anywhere')

        async def check_mail():
            if not services:
                ui.notify('이 환경에서는 점검할 수 없습니다.')
                return
            try:
                password = services['read_password'](config['email'])
            except Exception as exc:
                found['steps'] = [('비밀번호 읽기', False, f'{type(exc).__name__}: {exc}')]
                report_block.refresh()
                return
            ui.notify('메일 연결을 점검합니다…')
            found['steps'] = await nicerun.io_bound(helpers.connection_steps, config, password)
            report_block.refresh()

        async def check_codex():
            if not services:
                ui.notify('이 환경에서는 점검할 수 없습니다.')
                return
            ui.notify('Codex 상태를 확인합니다…')
            found['codex'] = await nicerun.io_bound(services['login_state'])
            report_block.refresh()

        with shell('/diagnose', token):
            ui.label('연결 실패와 분석 실패의 원인을 단계별로 확인합니다. '
                     '아무것도 수정하지 않고 읽기만 합니다.') \
                .style(f'color:{MUTED};font-size:12px;margin-bottom:10px')
            with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px'):
                ui.button('메일 연결 점검', on_click=check_mail).props('unelevated dense no-caps')
                ui.button('Codex 상태 점검', on_click=check_codex).props('outline dense no-caps')
            report_block()

    # 상담 -----------------------------------------------------------

    @ui.page('/chat')
    def chat_page(request: Request):
        if not allowed(request):
            refused()
            return
        account = account_of(config)
        mail_id = request.query_params.get('id') or ''
        row = store(directory).detail(mail_id) if mail_id else None
        busy = {'now': False}

        @ui.refreshable
        def thread():
            lines = list(store(directory).chat(account, mail_id)) if account else []
            if not lines:
                ui.label('질문을 입력하면 Codex가 답합니다. 답변은 한 번에 하나씩 오고, '
                         '분석이 돌고 있으면 그 뒤에 처리됩니다.') \
                    .style(f'color:{MUTED};font-size:12px')
            for role, text, at in lines:
                mine = role == 'user'
                with ui.element('div').style('display:flex;margin-top:8px;width:100%;'
                                             f"justify-content:{'flex-end' if mine else 'flex-start'}"):
                    with ui.element('div').style(
                            'display:block;max-width:70%;padding:8px 12px;border-radius:10px;'
                            f"background:{'#e8effd' if mine else CARD};"
                            f'border:1px solid {LINE}'):
                        ui.label(text).style(f'color:{INK};font-size:13px;'
                                             'white-space:pre-wrap;overflow-wrap:anywhere')
                        ui.label(line_text(at, '')[:14]).style(f'color:{MUTED};font-size:10px')
            if busy['now']:
                with ui.element('div').style('display:flex;gap:8px;align-items:center;'
                                             'margin-top:8px'):
                    ui.spinner(size='sm')
                    ui.label('Codex가 답하는 중입니다… 수십 초 걸릴 수 있습니다.') \
                        .style(f'color:{MUTED};font-size:12px')

        async def ask():
            question = (box.value or '').strip()
            if not question:
                return
            if not account:
                ui.notify('설정을 먼저 저장하세요.')
                return
            box.set_value('')
            store(directory).add_chat(account, 'user', question, mail_id)
            busy['now'] = True
            thread.refresh()
            history = [(role, text) for role, text, _ in
                       store(directory).chat(account, mail_id)][:-1]
            try:
                reply = await nicerun.io_bound(helpers.chat_reply, question, history,
                                               chat_context(row), config)
            except Exception as exc:
                reply = f'답하지 못했습니다: {type(exc).__name__}: {exc}'
            store(directory).add_chat(account, 'codex', reply, mail_id)
            busy['now'] = False
            thread.refresh()

        def wipe():
            store(directory).clear_chat(account, mail_id)
            thread.refresh()

        with shell('/chat', token):
            if row is not None:
                ui.label(f"이 메일에 대한 상담: {row['subject'] or '(제목 없음)'}") \
                    .style(f'color:{INK};font-size:13px;font-weight:600')
                ui.link('메일에서 보기', href('/mail', token, id=mail_id)) \
                    .style(f'color:{SERIES};font-size:12px;text-decoration:none')
            else:
                ui.label('메일을 지정하지 않은 일반 상담입니다. 메일 화면에서 넘어오면 '
                         '그 메일 내용을 함께 봅니다.').style(f'color:{MUTED};font-size:12px')
            ui.label('주의: 메일 본문이 로그인한 Codex 계정으로 전송됩니다. '
                     '대화는 이 PC의 데이터베이스에만 저장됩니다.') \
                .style(f'color:{MUTED};font-size:11px;margin-top:4px')
            with ui.element('div').style(f'display:block;margin-top:10px;padding:10px 12px;'
                                         f'border:1px solid {LINE};border-radius:8px;'
                                         'min-height:200px;max-height:440px;overflow-y:auto;'
                                         'width:100%'):
                thread()
            with ui.element('div').style('display:flex;gap:8px;margin-top:10px;'
                                         'align-items:flex-end'):
                box = ui.textarea(placeholder='질문을 입력하세요').classes('w-full')
                box.props('outlined autogrow dense')
                ui.button('보내기', on_click=ask).props('unelevated dense no-caps')
                ui.button('대화 지우기', on_click=wipe).props('flat dense no-caps')

    # 통계 -----------------------------------------------------------

    @ui.page('/stats')
    def stats_page(request: Request):
        if not allowed(request):
            refused()
            return
        span = {'days': 30}

        @ui.refreshable
        def body():
            data = stats_view(store(directory), account_of(config), date.today(), span['days'])
            with ui.element('div').style('display:flex;gap:8px;margin-bottom:10px'):
                for days in (7, 30, 90):
                    live = days == span['days']
                    ui.button(f'{days}일', on_click=lambda d=days: pick(d)) \
                        .props('flat dense no-caps' + ('' if live else ' text-color=grey'))
            with ui.element('div').style('display:grid;gap:10px;margin-bottom:6px;'
                                         'grid-template-columns:repeat(auto-fit,minmax(150px,1fr))'):
                for label, value in (('수집 총계', data['total']), ('분석 대기', data['waiting']),
                                     ('처리 완료', data['handled'])):
                    with ui.element('div').style(f'background:{CARD};border:1px solid {LINE};'
                                                 'border-radius:8px;padding:12px 14px'):
                        ui.label(label).style(f'color:{MUTED};font-size:12px')
                        ui.label(str(value)).style(f'color:{INK};font-size:24px;font-weight:700')
            if data['trend']:
                trend_block(data['trend'])
            with ui.element('div').style('display:grid;gap:24px;margin-top:10px;'
                                         'grid-template-columns:repeat(auto-fit,minmax(300px,1fr))'):
                with ui.element('div'):
                    section('메일 종류')
                    bar_list([(name, data['categories'][name]) for name in CATEGORIES])
                with ui.element('div'):
                    section('우선순위')
                    bar_list([(name, data['priorities'][name]) for name, _ in PRIORITIES], STATUS)

        def pick(days):
            span['days'] = days
            body.refresh()

        with shell('/stats', token):
            body()

    # 실행 -------------------------------------------------------------

    @ui.page('/run')
    def run_page(request: Request):
        if not allowed(request):
            refused()
            return

        @ui.refreshable
        def body():
            view = run_view(hub, directory, config, services)
            with ui.element('div').style('display:grid;gap:8px;margin-bottom:14px;'
                                         'grid-template-columns:repeat(auto-fit,minmax(190px,1fr))'):
                lamps = (('실행', view['label'], view['running']),
                         ('다음 확인', view['countdown'], view['running']),
                         ('메일 비밀번호', view['password'], view['password'] == '저장됨'))
                for label, value, good in lamps:
                    with ui.element('div').style(f'background:{CARD};border:1px solid {LINE};'
                                                 'border-radius:8px;padding:10px 12px'):
                        with ui.element('div').style('display:flex;align-items:center;gap:6px'):
                            ui.label('●').style(f"color:{LIVE if good else MUTED};font-size:11px")
                            ui.label(label).style(f'color:{MUTED};font-size:12px')
                        ui.label(value).style(f'color:{INK};font-size:14px;font-weight:600')
                for label, value in view['stamps'].items():
                    with ui.element('div').style(f'background:{CARD};border:1px solid {LINE};'
                                                 'border-radius:8px;padding:10px 12px'):
                        ui.label(label).style(f'color:{MUTED};font-size:12px')
                        ui.label(value).style(f'color:{INK};font-size:14px;font-weight:600')
            if not can_start:
                ui.label('창이 이미 실행 중입니다. 이 화면에서는 수집을 시작할 수 없습니다 — '
                         '수집기는 하나만 돕니다.') \
                    .style(f"color:{css_color(SOON)};font-size:12px")
            for blocker in view['blockers']:
                ui.label('설정 확인: ' + blocker) \
                    .style(f"color:{css_color(URGENT)};font-size:12px")
            if view['message']:
                ui.label(view['message']).style(f'color:{INK};font-size:13px;margin-top:4px')
            section('기록')
            if not view['lines']:
                ui.label('아직 기록이 없습니다.').style(f'color:{MUTED};font-size:12px')
            else:
                with ui.element('div').style(f'display:block;padding:10px 12px;background:{CARD};'
                                             f'border:1px solid {LINE};border-radius:8px;'
                                             'font-size:11px;line-height:1.8;max-height:360px;'
                                             'overflow-y:auto;width:100%'):
                    for text in view['lines']:
                        ui.label(text).style(f'color:{MUTED};white-space:pre-wrap;'
                                             'overflow-wrap:anywhere')

        def begin():
            if not can_start:
                ui.notify('메일 도우미 창이 이미 실행 중입니다. 수집기는 하나만 돌 수 있습니다. '
                          '창에서 시작하거나, 창을 닫고 이 화면을 다시 여세요.')
                return
            errors = field_errors(config)
            if errors:
                ui.notify('설정을 먼저 확인하세요: ' + ' '.join(errors.values()))
                return
            if not services or not services.get('read_password'):
                ui.notify('이 환경에서는 워커를 시작할 수 없습니다. Windows에서 실행하세요.')
                return
            try:
                services['read_password'](config['email'])
            except LookupError:
                ui.notify('메일 전용 비밀번호가 없습니다. 설정에서 입력하세요.')
                return
            except Exception as exc:
                ui.notify(f'자격 증명을 읽지 못했습니다: {type(exc).__name__}: {exc}')
                return
            if hub.start(dict(config)):
                hub.log('시작했습니다. 창을 닫아도 계속 실행됩니다.')
            body.refresh()

        def stop():
            if hub.halt():
                hub.log('중지 요청됨. 현재 메일·분석·엑셀 작업이 끝나면 멈춥니다.')
            body.refresh()

        def now():
            if hub.wake():
                hub.log('지금 확인을 요청했습니다.')
            else:
                ui.notify('실행 중이 아닙니다.')

        def reveal(path, label):
            try:
                os.startfile(str(path))          # local app: the server is this PC
            except Exception as exc:
                ui.notify(f'{label}을 열지 못했습니다: {type(exc).__name__}: {exc}')

        async def test():
            if not services:
                ui.notify('이 환경에서는 연결 테스트를 할 수 없습니다.')
                return
            try:
                password = services['read_password'](config['email'])
            except Exception as exc:
                ui.notify(f'비밀번호를 읽지 못했습니다: {exc}')
                return
            ui.notify('연결 테스트 중…')
            try:
                message = await nicerun.io_bound(services['check_connection'], config, password)
            except Exception as exc:
                hub.log(f'연결 테스트 실패: {type(exc).__name__}: {exc}')
                ui.notify(f'실패: {exc}')
            else:
                hub.log(message)
                ui.notify(message)
            body.refresh()

        def codex_login():
            if not services:
                ui.notify('이 환경에서는 Codex 로그인을 열 수 없습니다.')
                return
            try:
                subprocess.Popen(services['codex_command']() + ['login'],
                                 env=services['codex_environment'](),
                                 creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
                hub.log('Codex 로그인 창을 열었습니다. 로그인 후 상태를 다시 확인하세요.')
            except Exception as exc:
                ui.notify(f'Codex 로그인을 열지 못했습니다: {exc}')

        with shell('/run', token):
            with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px'):
                ui.button('시작', on_click=begin).props('unelevated dense no-caps')
                ui.button('중지', on_click=stop).props('outline dense no-caps')
                ui.button('지금 확인', on_click=now).props('flat dense no-caps')
                ui.button('연결 테스트', on_click=test).props('flat dense no-caps')
                ui.button('Codex 로그인', on_click=codex_login).props('flat dense no-caps')
                ui.button('엑셀 열기',
                          on_click=lambda: reveal(config.get('workbook', ''), '엑셀 파일')) \
                    .props('flat dense no-caps')
                ui.button('데이터 폴더', on_click=lambda: reveal(directory, '데이터 폴더')) \
                    .props('flat dense no-caps')
            body()
            ui.timer(REFRESH_SECONDS, body.refresh)

    # 설정 -------------------------------------------------------------

    @ui.page('/settings')
    def settings_page(request: Request):
        if not allowed(request):
            refused()
            return
        values = {key: str(config.get(key, '')) for key, _ in FIELDS if key != 'password'}
        inputs, notes = {}, {}

        def validate():
            errors = field_errors(values)
            for key, box in inputs.items():
                message = errors.get(key, '')
                notes[key].set_text(message)
                notes[key].style(f"color:{css_color(URGENT)};font-size:11px"
                                 if message else f'color:{MUTED};font-size:11px')
            return errors

        def save():
            if validate():
                ui.notify('빨간 글씨로 표시된 항목을 고친 뒤 저장하세요.')
                return
            try:
                updated = normalize(values)
            except ValueError as exc:
                ui.notify(str(exc))
                return
            temp = config_path.with_suffix('.tmp')
            # Merge: webhook and the update keys are not on this form and must survive.
            temp.write_text(json.dumps({**config, **updated}, ensure_ascii=False, indent=2),
                            encoding='utf-8')
            temp.replace(config_path)
            config.update(updated)
            hub.log('설정을 저장했습니다.')
            ui.notify('설정을 저장했습니다.')

        def save_password():
            secret = (password_box.value or '').strip()
            if not secret:
                ui.notify('비밀번호를 입력하세요.')
                return
            if not services:
                ui.notify('이 환경에서는 자격 증명을 저장할 수 없습니다.')
                return
            remember_secret(secret)
            try:
                services['save_password'](values['email'], secret)
                stored = services['read_password'](values['email'])
            except Exception as exc:
                ui.notify(f'저장 실패: {type(exc).__name__}: {exc}')
                return
            password_box.set_value('')
            ui.notify('비밀번호를 저장했습니다.' if stored == secret
                      else '경고: 저장된 값이 입력과 다릅니다. 다시 입력해 보세요.')

        def drop_password():
            if not services or not services.get('delete_password'):
                ui.notify('이 환경에서는 자격 증명을 지울 수 없습니다.')
                return
            try:
                services['delete_password'](values['email'])
            except LookupError:
                ui.notify('저장된 비밀번호가 없습니다.')
                return
            except Exception as exc:
                ui.notify(f'삭제 실패: {type(exc).__name__}: {exc}')
                return
            hub.log('저장된 메일 전용 비밀번호를 삭제했습니다.')
            ui.notify('삭제했습니다.')

        def unskip():
            update.remember(config_path, {'update_skip': ''})
            config['update_skip'] = ''
            ui.notify('건너뛴 버전을 초기화했습니다. 다음 확인에서 다시 알려 드립니다.')
            skip_row.refresh()

        with shell('/settings', token):
            with ui.element('div').style('display:grid;gap:12px;max-width:620px'):
                for key, label in FIELDS:
                    if key == 'password':
                        continue
                    ui.label(label).style(f'color:{INK};font-size:13px;font-weight:600')
                    box = ui.input(value=values[key]).classes('w-full')
                    box.props('dense outlined')
                    box.on_value_change(lambda event, name=key: (values.update({name: event.value or ''}),
                                                                 validate()))
                    inputs[key] = box
                    notes[key] = ui.label('').style(f'color:{MUTED};font-size:11px')
                ui.button('저장', on_click=save).props('unelevated dense no-caps')

                section('메일 전용 비밀번호')
                ui.label('Windows 자격 증명에 저장됩니다. 설정 파일에는 기록되지 않습니다.') \
                    .style(f'color:{MUTED};font-size:11px')
                password_box = ui.input(password=True, placeholder='입력 후 저장').classes('w-full')
                password_box.props('dense outlined')
                with ui.element('div').style('display:flex;gap:8px;margin-top:6px'):
                    ui.button('비밀번호 저장', on_click=save_password).props('unelevated dense no-caps')
                    ui.button('저장된 비밀번호 삭제', on_click=drop_password) \
                        .props('outline dense no-caps')

                section('업데이트')

                @ui.refreshable
                def skip_row():
                    skipped = config.get('update_skip') or ''
                    ui.label(f'건너뛴 버전: {skipped}' if skipped else '건너뛴 버전이 없습니다.') \
                        .style(f'color:{MUTED};font-size:12px')
                    if skipped:
                        ui.button('다시 알림 받기', on_click=unskip).props('flat dense no-caps')

                skip_row()

                section('이 컴퓨터')
                for label, value in (('버전', __version__), ('데이터 폴더', str(directory)),
                                     ('설정 파일', str(config_path)),
                                     ('데이터베이스', str(directory / 'mail.db'))):
                    with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap'):
                        ui.label(label).style(f'color:{MUTED};font-size:12px;min-width:80px')
                        ui.label(value).style(f'color:{INK};font-size:12px;'
                                              'overflow-wrap:anywhere')
                ui.label('메일 본문은 분석을 위해 로그인한 Codex 계정으로 전송됩니다.') \
                    .style(f'color:{MUTED};font-size:11px;margin-top:8px')
            validate()


def serve(directory, config, token=None, host='127.0.0.1', port=None, native=False, show=False,
          hub=None, services=None, config_path=None, can_start=True):
    """Loopback only: the page serves mail content and must not be reachable off the PC."""
    from nicegui import app, ui
    token = token or new_token()
    port = port or open_port(host)
    build(directory, config, token, hub, services, config_path, can_start)
    app.on_shutdown(release_store)
    # Whatever opens the page has to carry the token, or it lands on the refusal.
    # nicegui's `show` takes a path (it appends it to the root URL), and the native
    # window's URL comes from window_args, which it merges over its own.
    target = f'/?t={token}' if token else '/'
    if native:
        app.native.window_args['url'] = f'http://{host}:{port}{target}'
    # flush: a redirected stdout is block-buffered, and spike.ps1 reads this line
    # out of the log to know the server is up.
    print(f'메일 도우미 현황: http://{host}:{port}{target}', flush=True)
    ui.run(host=host, port=port, reload=False, show=target if show else False,
           native=native, dark=False, title=f'메일 도우미 {__version__}',
           storage_secret=token, favicon='📬')
