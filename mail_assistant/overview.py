"""Numbers for the 현황 tab, computed from database rows. No UI, no COM.

The Excel dashboard keeps its formulas; the app counts the same things here so it
can show them without a spreadsheet.
"""
import json
from datetime import date, timedelta

from .calendar_sheet import collect, plain_title
from .core import (HANDLED, PRIORITY_ORDER, PROGRESS, WAIT_DAYS, day_bounds,
                   event_key, local_text, waiting_days)
from .core import workbook_rows
from .dashboard import CATEGORIES, PRIORITIES

DUE_DAYS = 7
RECENT_DAYS = 7
UPCOMING = 8
# What 오늘의 AI 브리핑 is allowed to send. The input is analysed results, never mail
# bodies: analyze() already spent the 60,000-character budget on those one at a time,
# and thirty of them in one prompt is the 240-second timeout with nothing to show.
# Roughly 8-12KB of JSON at these caps, which is where the answer stops getting
# sharper and starts getting vaguer.
BRIEF_OPEN = 30
BRIEF_EVENTS = 15
BRIEF_DRAFTS = 10
# 답장 대기는 이미 경과일로 세워져 있으니 앞의 열 건이 가장 오래 기다린 열 건이다.
BRIEF_WAIT = 10
BRIEF_TEXT = 200
# Nothing before this hour: a briefing written at 03:00 describes yesterday and is
# what the reader finds at nine.
BRIEF_HOUR = 8


def analysed(rows):
    return [row for row in rows if row['result']]


def results(rows):
    for row in analysed(rows):
        try:
            yield row, json.loads(row['result'])
        except ValueError:
            continue


def event_lines(events):
    """직접 추가한 일정 in the 일정 sheet's own row shape.

    Built here rather than as a second kind of entry so collect() decides 마감/시작,
    the clock and the sort for both: a manually added deadline that sorted differently
    from an analysed one would be the same day drawn two ways.
    """
    return [[f'{event_key(row["id"])}:0', event_key(row['id']), row['title'],
             row['start'], row['deadline'], row['note'], '', '']
            for row in events if str(row['title'] or '').strip()]


def schedule_entries(rows, events=()):
    """Reuse the 일정 sheet shape so collect()/month_grid() work unchanged."""
    lines = []
    for row in analysed(rows):
        try:
            lines.extend(workbook_rows(row)['일정'])
        except (ValueError, KeyError, TypeError):
            continue
    lines.extend(event_lines(events))
    return collect(lines)


def deadlines(events, today, within=DUE_DAYS, handled=()):
    """(date, entry) for deadlines from today up to `within` days, handled mails dropped."""
    limit = today + timedelta(days=within)
    return [(day, entry) for day in sorted(events) if today <= day <= limit
            for entry in events[day] if entry.kind != '시작' and entry.mail_id not in handled]


def past_due(events, today, handled=(), limit=UPCOMING):
    """Missed deadlines, nearest first: the 7-day window above would hide them entirely."""
    return [(day, entry) for day in sorted(events, reverse=True) if day < today
            for entry in events[day] if entry.kind != '시작' and entry.mail_id not in handled][:limit]


def due_window(events, today, within=DUE_DAYS, limit=UPCOMING, handled=()):
    """Missed then upcoming deadlines in date order, finished ones kept in view.

    deadlines() and past_due() drop handled mail because the cards count what is left.
    This list is the one place a finished deadline still has to show: it is a checklist,
    and ticking a row must not delete the very thing whose progress it just moved. An
    unhandled miss never falls off however old it is; a finished one only lingers while
    it is inside the same window the panel is showing.
    """
    floor = today - timedelta(days=within)
    missed = [(day, entry) for day, entry in past_due(events, today, limit=None)
              if entry.mail_id not in handled or day >= floor][:limit]
    return list(reversed(missed)) + deadlines(events, today, within=within)[:limit]


def failures(rows):
    """Analysed-and-failed, which is the 실패 filter's own rule (core.STATE_SQL)."""
    return sum(1 for row in rows if not row['result'] and row['attempts'])


def oldest_open(rows, today, handled=()):
    """Days since the oldest analysed mail nobody has closed, or None when there is none.

    The count beside it says how many are open; this says how long the worst one has
    been open, which is the half a total cannot show.
    """
    days = []
    for row in analysed(rows):
        if row['id'] in handled:
            continue
        try:
            days.append(date.fromisoformat(local_text(row['received'], '%Y-%m-%d')))
        except ValueError:
            continue
    # max(0, …): `received` is the collection time, and a PC whose clock is behind
    # the mail server's produces a mail from tomorrow. '-3일 경과' is not a thing.
    return max(0, (today - min(days)).days) if days else None


def waiting_replies(rows, today, handled=(), days=WAIT_DAYS):
    """답장이 필요한데 완료 표시가 없는 채로 days일이 지난 메일, 오래 기다린 것부터.

    Reads the `reply_needed` *column*, never the stored JSON, because the 메일 화면's
    own 답장 대기 filter is SQL over that column (core.state_where): a card that
    counted one source and opened a list built from the other would be two answers to
    one question, which is the same rule that put category and priority in columns.

    core.waiting_days() is that one judgement, shared with the tkinter list's own
    filter, so 'is this one waiting' is decided in a single place whatever screen asks.
    """
    waiting = []
    for row in rows:
        elapsed = waiting_days(row, today)
        if elapsed is None or elapsed < days or row['id'] in handled:
            continue
        waiting.append({'id': row['id'], 'subject': row['subject'] or '(제목 없음)',
                        'sender': row['sender'] or '', 'priority': row['priority'] or '',
                        'received': row['received'] or '', 'days': elapsed,
                        'progress': row['handled'] == PROGRESS})
    # 오래 기다린 것부터. 우선순위가 아니라 경과일로 세우는 것이 이 목록의 전부다 —
    # 급한 것은 긴급·높음 카드가 이미 말하고 있고, 여기서만 볼 수 있는 사실은
    # '이것이 제일 오래 서 있다'뿐이다.
    waiting.sort(key=lambda item: (-item['days'], item['received']))
    return waiting


def counts_by(rows, field, allowed):
    counted = {name: 0 for name in allowed}
    for _, result in results(rows):
        value = result.get(field, '')
        if value in counted:
            counted[value] += 1
    return counted


def recent_days(rows, today, days=RECENT_DAYS):
    counted = {(today - timedelta(days=offset)).isoformat(): 0 for offset in range(days)}
    for row in rows:
        key = local_text(row['received'], '%Y-%m-%d')
        if key in counted:
            counted[key] += 1
    return [(day, counted[day]) for day in sorted(counted)]


def trend(store, account, today, days=RECENT_DAYS):
    """[(day, {collected, analyzed, exported})] oldest first, by Korean calendar day.

    Store.day_counts() existed and was tested but no screen ever asked for it.
    """
    rows = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        rows.append((day.isoformat(), store.day_counts(account, *day_bounds(day))))
    return rows


def review_queue(rows):
    """답변이 필요한데 사람이 초안을 손대지 않은 메일. 카드와 검토 큐가 같은 판정을 쓴다.

    '답장이 필요한가'를 묻는 곳이 이제 셋이다 — 이 큐, 답장 대기 카드, 메일 화면의
    필터 — 그래서 셋 다 reply_needed 컬럼 하나를 읽는다. 저장된 JSON에서 다시 꺼내면
    backfill이 한쪽만 건드린 날 두 화면이 다른 답을 하게 된다.
    """
    return [row for row in analysed(rows)
            if row['reply_needed'] == 1 and not (row['draft_edit'] or '').strip()]


def review_pending(rows):
    return len(review_queue(rows))


def clip(value, limit=BRIEF_TEXT):
    """One line of an analysed field, short enough that thirty of them still fit."""
    text = ' '.join(str(value or '').split())
    return text[:limit] + '…' if len(text) > limit else text


def brief_rank(row, result):
    """긴급 first, then the oldest — the two things that decide what gets read out."""
    try:
        rank = PRIORITY_ORDER.index(result.get('priority', ''))
    except ValueError:
        rank = len(PRIORITY_ORDER)
    return rank, row['received'] or ''


def briefing_input(rows, today, events=(), within=DUE_DAYS, trend_rows=()):
    """What 오늘의 AI 브리핑 sends to Codex: the analysed numbers, never the mail.

    Every mail here has already been through analyze(), so the briefing is a second
    pass over answers rather than a second reading of the mailbox — one small call a
    day against the one Codex slot analysis also needs. The caps are in the payload as
    `truncated`: a model that saw thirty of eighty-seven must be able to say so rather
    than write '전체적으로 조용합니다' about the thirty.
    """
    data = overview(rows, today, within=within, events=events)
    handled = data['handled']
    open_mail = sorted(((row, result) for row, result in results(rows)
                        if row['id'] not in handled), key=lambda pair: brief_rank(*pair))
    drafts = review_queue(rows)
    waiting = data['reply_wait']
    deadline_rows = [(day, entry) for day, entry in data['due_window']
                     if entry.mail_id not in handled]
    return {
        'today': today.isoformat(),
        'window_days': within,
        'counts': {**data['cards'], '분석 실패': data['failed'],
                   '수집 전체': data['total'], '분석 대기': data['waiting'],
                   '답장 대기': len(waiting), '처리 완료': len(handled)},
        'oldest_open_days': data['oldest'],
        'deadlines': [{'day': day.isoformat(), 'kind': entry.kind,
                       'title': clip(plain_title(entry.label)),
                       'mail_id': entry.mail_id, 'overdue': day < today}
                      for day, entry in deadline_rows[:BRIEF_EVENTS]],
        'open_mail': [{'mail_id': row['id'], 'received': (row['received'] or '')[:10],
                       'subject': clip(row['subject'] or '(제목 없음)', 80),
                       'sender': clip(row['sender'], 80),
                       'priority': result.get('priority', ''),
                       'category': result.get('category', ''),
                       'requests': clip(result.get('requests', '')),
                       'next_action': clip(result.get('next_action', ''))}
                      for row, result in open_mail[:BRIEF_OPEN]],
        'unedited_drafts': [clip(row['subject'] or '(제목 없음)', 80)
                            for row in drafts[:BRIEF_DRAFTS]],
        # 브리핑이 '오늘 무엇이 왔나' 말고 '내가 무엇을 붙잡고 있나'를 말할 수 있는
        # 유일한 자리. days가 문장의 전부이므로 제목보다 먼저 온다.
        'waiting_reply': [{'mail_id': row['id'], 'days': row['days'],
                           'subject': clip(row['subject'], 80),
                           'sender': clip(row['sender'], 80),
                           'priority': row['priority']}
                          for row in waiting[:BRIEF_WAIT]],
        'daily': [{'day': day, **counts} for day, counts in trend_rows],
        'truncated': {'open_mail': {'shown': min(len(open_mail), BRIEF_OPEN),
                                    'total': len(open_mail)},
                      'deadlines': {'shown': min(len(deadline_rows), BRIEF_EVENTS),
                                    'total': len(deadline_rows)},
                      'unedited_drafts': {'shown': min(len(drafts), BRIEF_DRAFTS),
                                          'total': len(drafts)},
                      'waiting_reply': {'shown': min(len(waiting), BRIEF_WAIT),
                                        'total': len(waiting)}},
    }


def briefing_due(stamp, today, hour, pending, asked=False):
    """하루 한 번, 아침 이후, 분석 대기가 없을 때. 수동 요청은 시각만 건너뛴다.

    Analysis always wins the Codex slot: a briefing that queued ahead of it would hold
    the only process for up to 240 seconds at exactly the hour the morning's mail is
    waiting to be read. `asked` is the button, and it skips the clock and the stamp but
    not that — which is why the screen says so rather than the button being disabled.
    """
    if pending:
        return False
    if asked:
        return True
    return hour >= BRIEF_HOUR and stamp != today.isoformat()


def overview(rows, today, within=DUE_DAYS, events=()):
    """events is 직접 추가한 일정; they join the analysed ones and are counted with them."""
    handled = {row['id'] for row in rows if row['handled'] == HANDLED}
    # A manual event owns no mail, so it carries its own 처리 상태 — and lands in the
    # same set, because every panel below asks 'is this one finished' of one set.
    handled |= {event_key(row['id']) for row in events if row['handled'] == HANDLED}
    events = schedule_entries(rows, events)
    due = deadlines(events, today, within=within, handled=handled)
    priorities = counts_by(rows, 'priority', [name for name, _ in PRIORITIES])
    return {
        'cards': {
            '미처리 메일': sum(1 for row in analysed(rows) if row['id'] not in handled),
            '긴급·높음': priorities.get('긴급', 0) + priorities.get('높음', 0),
            f'{within}일 내 마감': len(due),
            '검토 전 초안': review_pending(rows),
        },
        'categories': counts_by(rows, 'category', CATEGORIES),
        'priorities': priorities,
        'recent': recent_days(rows, today),
        'upcoming': due[:UPCOMING],
        'past_due': past_due(events, today, handled=handled),
        'due_window': due_window(events, today, within=within, handled=handled),
        'within': within,
        'events': events,
        'handled': handled,
        'total': len(rows),
        'waiting': sum(1 for row in rows if not row['result']),
        # Beside the four cards, not among them: the window in app.py lays those out
        # as a fixed four and a fifth key would land in none of its labels. 답장 대기
        # is here for the same reason, and is a list rather than a count because the
        # panel that draws it needs the rows and the card only needs len().
        'failed': failures(rows),
        'reply_wait': waiting_replies(rows, today, handled),
        'oldest': oldest_open(rows, today, handled),
    }
