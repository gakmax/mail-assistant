"""The 대시보드 screen as a NiceGUI page: the 1단계 spike of UI-PLAN.md.

nicegui is imported inside the functions that need it, so the shaping helpers below
and their tests keep working on a machine that has not installed it.
"""
import asyncio
import json
import os
import re
import secrets
import socket
import threading
import time
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from . import __version__
from .calendar_sheet import COLORS, parse_day, plain_title
from .core import (ANALYZING, FAILED, HANDLED, LIST_LIMIT, PROGRESS, ROOM_MARK, SORTS,
                   STATES, Store, WAIT_DAYS, WAITING, account_key, event_row_id,
                   is_event_key, local_text,
                   looks_foreign, now, parse_mail, row_view, state_of)
from .dashboard import CATEGORIES, PRIORITIES, describe
from .excel import mailto
from .hub import line_text
from .overview import (BRIEF_HOUR, DUE_DAYS, RECENT_DAYS, UPCOMING, overview, past_due,
                       results, review_queue, trend)
from .services import BODY_LIMIT, DRAFT_TONES, DRAFT_WAYS
from .settings import (DEFAULTS, FIELDS, GRADE_NOTE, NO_MODELS, RECOMMEND_WHY,
                       field_errors, model_label, model_rows, normalize, read_models)
from .style import CALM, DASH_GREEN, LINK, NEUTRAL, SOON, URGENT, css_color
from .usage import (CALM as USAGE_CALM, FULL as USAGE_FULL, WARN as USAGE_WARN,
                    Meter as UsageMeter)
# The one module here that is not a screen's: update.py is stdlib-only, so the
# beat's interval can be shared with app.py without either importing a toolkit.
from .update import WATCH_SECONDS

SERIES = css_color(LINK)      # one hue: every count bar measures the same thing
INK = '#18181b'
SUBTLE = '#52525b'            # body text that is not a heading and not a side note
MUTED = css_color(CALM)
CARD = '#ffffff'              # a card floats on BG; anything inset uses SUNKEN
SUNKEN = '#f7f8fa'
BG = '#f4f5f7'
LINE = '#e4e4e7'
HAIR = '#eff0f2'              # the lighter rule inside a card, where LINE reads heavy
BRAND = css_color(LINK)
BRAND_SOFT = '#e8effc'
OK = css_color(DASH_GREEN)
# A status palette, not a categorical one: every bar is labelled, and 보통·낮음 are
# grey on purpose so 긴급·높음 are the only two colours competing for attention.
STATUS = {'긴급': css_color(URGENT), '높음': css_color(SOON),
          '보통': css_color(NEUTRAL), '낮음': css_color(CALM)}
# Shown only when it is not zero: a card reading '분석 실패 0' every day is how a
# reader learns to stop looking at that corner of the screen.
FAILED_CARD = '분석 실패'
# A card wears a tone only while its number is actionable. The number itself stays
# ink, so colour is never the only thing saying '이건 봐야 한다'.
CARD_TONES = {'긴급·높음': css_color(URGENT), f'{DUE_DAYS}일 내 마감': css_color(SOON),
              '검토 전 초안': SERIES, WAITING: css_color(SOON),
              FAILED_CARD: css_color(URGENT)}
REFRESH_SECONDS = 5.0
# 실행 and 메일 read their own numbers every second: both are screens somebody opens
# *because* something is moving, and five seconds of a still picture reads as a hang.
# 대시보드 keeps the slower beat — its charts and tallies are a day's shape, not a
# pulse — but repaints at once when another screen says it changed (Hub.touch).
LIVE_SECONDS = 1.0
RUN_LINES = 8
# The 사용량 chip's own beat, and the one-shot that fills it when a page opens. The
# number moves when an analysis runs — minutes apart, never seconds — and the read is
# a process launch, so it is nothing like the five-second repaint beside it. Meter's
# own gate is what actually decides; this only says how soon after it opens somebody
# hears, exactly as WATCH_SECONDS does for the update check.
USAGE_SECONDS = 120.0
USAGE_FIRST = 1.5
# Three states and no green: a quota that is fine is not an achievement to light up,
# it is the absence of news — the same reason the 분석 실패 card is missing at 0.
USAGE_TONES = {USAGE_CALM: MUTED, USAGE_WARN: css_color(SOON), USAGE_FULL: css_color(URGENT)}
# The 사용량 half of settings.GRADES, in colour. 성능 stays grey on purpose: somebody
# opening this list is choosing what it will cost them, and two accented chips on one
# row is the 분석 결과 wall of headings again. A test holds this against GRADES.
COST_TONES = {'사용량 많음': css_color(SOON), '사용량 보통': SUBTLE, '사용량 적음': OK}
# The frame opens at this size, not at pywebview's 800x600: 대시보드 is a two-column
# layout and 메일 is a table with six columns, and both start inside a scrollbar at
# the default. WINDOW_MIN is as small as the layout still reads at.
WINDOW = (1440, 940)
WINDOW_MIN = (1080, 720)
WINDOW_MARGIN = 80          # taskbar, title bar, and room to grab an edge
WINDOW_FLOOR = (640, 480)   # no desktop is smaller; a floor stops a silly screen value
# (path, label, Material icon). The icons ship with Quasar, so nothing is fetched.
PAGES = (('/', '대시보드', 'dashboard'), ('/mail', '메일', 'mail'), ('/calendar', '일정', 'event'),
         ('/todo', '할 일', 'checklist'), ('/drafts', '초안', 'drafts'),
         ('/memo', '메모', 'sticky_note_2'),
         ('/chat', '상담', 'forum'), ('/stats', '통계', 'insights'),
         ('/run', '실행', 'play_circle'), ('/settings', '설정', 'settings'))
# The sidebar's groups, in the order they are drawn. Ten destinations in one flat
# row read as ten; grouped they read as four questions. 대시보드 keeps no heading —
# it is where the window opens, and a label over a single item is noise. Only paths
# live here, so PAGES stays the one list of destinations; a test holds the two equal,
# because a page added there and forgotten here would be reachable by URL and by
# nothing else on screen.
NAV_GROUPS = (('', ('/',)),
              ('업무', ('/mail', '/calendar', '/todo', '/drafts', '/memo')),
              ('도움', ('/chat', '/stats')),
              ('시스템', ('/run', '/settings')))
# The sidebar with labels, and the same sidebar as icons only. The nine links used to
# sit in the header with overflow-x:auto and scrollbar-width:none, so anything else
# put in that band pushed 설정 off the end with nothing on screen saying so.
SIDE_WIDE = 216
SIDE_RAIL = 64
# WINDOW_MIN is 1080 wide and the 메일 table has six columns: below this the labels go
# back to the content, which is what shoot.ps1 caught them being squeezed out of.
SIDE_BREAK = 1200
# The fold is one gesture, so everything that moves with it shares one curve and one
# duration: a width that slides while its labels blink reads as two things happening.
SIDE_EASE = '.22s cubic-bezier(.4,0,.2,1)'
# Said once, under the two pickers: both are things the reader would otherwise have
# to press the button to find out.
DRAFT_NOTE = '받은 메일과 같은 언어로 씁니다. 서명과 연락처는 넣지 않습니다.'
# A heading row that may carry a dense control — the 원문/한글 번역 toggle is the
# tallest of them — beside one that carries nothing but the heading.
HEAD_ROW = 30
# .ma-side__group is a heading in the sidebar and a 1px rule in the rail, and no height
# animates from auto — this is what the heading measures, pinned so that it can.
SIDE_GROUP = 35
# The 상담 page fills the frame rather than sitting in the top of it: .ma-bar (56px and
# its hairline) plus .ma-page's own 20px/56px padding is everything above and below it.
CHAT_CHROME = 57 + 20 + 56
NAV_BADGE_MAX = 99
# Monday first, and never through strftime: Windows encodes a format string with the
# locale codec, so a Korean pattern raises UnicodeEncodeError off a Korean PC.
WEEKDAYS = ('월', '화', '수', '목', '금', '토', '일')
# The 대시보드 cards. '…일 내 마감' carries the window in its name, so it is matched by suffix.
CARD_ICONS = {'미처리 메일': 'inbox', '긴급·높음': 'priority_high', '검토 전 초안': 'edit_note',
              WAITING: 'reply', FAILED_CARD: 'error_outline'}
# The 메일 list's columns, in the order the table draws them. Every key is also a key
# of core.SORTS, which is what lets the header itself be the sort control — a test
# holds the two together, because a header with no SQL behind it would sort nothing.
LIST_FIELDS = (('received', '수신'), ('sender', '발신자'), ('subject', '제목'),
               ('category', '종류'), ('priority', '우선순위'), ('state', '상태'))
# Columns a first click should sort from ㄱ. The rest read newest or most urgent first.
TEXT_SORTS = ('subject', 'sender', 'category')
# The 우선순위 names on their own. PRIORITIES carries a colour for Excel's chart, and
# the list filter wants only the words — the same set the bars beside it are drawn from.
PRIORITY_NAMES = tuple(name for name, _ in PRIORITIES)
# The 메일 목록's own filters, and what a 대시보드 bar sets when it is clicked. The value
# is the column, so a link arriving with ?category=공지 needs no translation.
BAR_FILTERS = (('category', '종류', CATEGORIES), ('priority', '우선순위', PRIORITY_NAMES))
DEFAULT_LIST = {'query': '', 'state': '', 'category': '', 'priority': '',
                'sort': 'received', 'desc': True,
                'page': 0, 'per': LIST_LIMIT, 'selected': None}
WINDOWS = (7, 14, 30)           # the 마감 windows the 대시보드 card offers
TREND_LABELS = (('collected', '수집'), ('analyzed', '분석'), ('exported', '반영'))
TREND_TONES = {'collected': css_color(LINK), 'analyzed': css_color(NEUTRAL),
               'exported': css_color(DASH_GREEN)}
# The kanban's three columns. '' and 처리 already existed; 진행 is the new middle one.
COLUMNS = (('', '대기'), (PROGRESS, '진행'), (HANDLED, '완료'))
# Tag colours for the 상태 column. 'N회 실패' carries its count and is matched separately,
# in red — the 분석 실패 card is already red, and amber now belongs to 분석 중, which is
# the one state in this column that is happening right now.
STATE_TONES = {HANDLED: css_color(DASH_GREEN), PROGRESS: css_color(LINK),
               '미처리': '#52525b', '분석 대기': css_color(CALM),
               ANALYZING: css_color(SOON)}
# The kanban lane dots. Same three states, same three colours as the 상태 tags above.
LANE_TONES = {'': css_color(CALM), PROGRESS: css_color(LINK), HANDLED: css_color(DASH_GREEN)}

# 메모 paper. (key, name, ground, edge) — the only palette in this file that is not
# style.py's, because the window has no 메모 화면 to drift apart from and Excel never
# sees one. The grounds are deliberately washed out: Windows' own sticker yellow on
# a #f4f5f7 page, beside a brand blue, is the loudest thing on the screen and every
# note then shouts equally, which is the same arithmetic as accenting all four
# 분석 결과 blocks. Colour is the whole of a memo's structure — there is no title, no
# date field, no state — so it has to stay a quiet signal rather than an alarm.
NOTE_COLORS = (('yellow', '노랑', '#fdf3d0', '#e8d49a'),
               ('pink', '분홍', '#fce4ea', '#efb9c8'),
               ('green', '초록', '#e2f3e8', '#a8d7bb'),
               ('blue', '파랑', '#e2eefc', '#a9c9f2'),
               ('purple', '보라', '#ece6fb', '#c3b4ef'),
               ('gray', '회색', '#eef0f3', '#cdd0d6'))
DEFAULT_NOTE_COLOR = NOTE_COLORS[0][0]
# The 메모 화면's three segments. 'mail' is the one that earns the page: a memo that
# belongs to a mail is the thing 할 일 cannot hold.
NOTE_FILTERS = (('all', '전체'), ('pin', '고정'), ('mail', '메일에 붙임'))
# How many pinned memos the 대시보드 shows. The panel is a reminder, not the wall.
PINNED_ON_HOME = 3
LOCAL = threading.local()

# Pretendard, served from /vendor like FullCalendar — never a CDN. One variable file
# covers every weight the pages use, and it carries its own Latin, so 맑은 고딕 and
# Segoe UI are only the fallback for a machine where the asset went missing.
FONT_STACK = ("'Pretendard Variable',Pretendard,'Segoe UI Variable Text','Segoe UI',"
              "system-ui,'맑은 고딕','Malgun Gothic','Apple SD Gothic Neo',sans-serif")
CHART_FONT = FONT_STACK
# Pretendard has no monospace cut, and a code span in a Codex answer has to line up.
MONO = "ui-monospace,'Cascadia Mono',Consolas,monospace"
FONT_FILE = 'PretendardVariable.woff2'
# Preloaded, not merely declared: ECharts draws its labels into a canvas once and does
# not repaint when a font arrives late, so the file has to be in flight before the
# first chart exists. It is a local read, so this costs nothing but ordering.
FONT_PRELOAD = (f'<link rel="preload" href="/vendor/pretendard/{FONT_FILE}" as="font" '
                'type="font/woff2" crossorigin>')

# The reader's own choice of sidebar width, kept in the browser and nowhere else.
# Three states and not two: 'rail' and 'wide' are explicit, and the absence of both is
# the breakpoint's own answer — which is what lets a narrow window still be opened out.
RAIL_KEY = 'ma-side'
# Read before Vue mounts, so a sidebar that was left collapsed is collapsed in the
# first frame instead of snapping shut once the page is up.
RAIL_BOOT = f"""<script>
(function () {{
  try {{
    var choice = localStorage.getItem('{RAIL_KEY}');
    if (choice === 'rail' || choice === 'wide')
      document.documentElement.classList.add('ma-' + choice);
  }} catch (err) {{}}
}})();
</script>"""
# Client-side only, for the reason the kanban's dragover is: the server has nothing to
# do with how wide somebody wants their navigation, and a round trip per click would
# buy a repaint of a class name. It reads the *effective* state — class, or the
# breakpoint when no class was chosen — so one press always does the visible thing.
RAIL_TOGGLE = f"""(e) => {{
  const root = document.documentElement;
  const narrow = window.matchMedia('(max-width:{SIDE_BREAK - 1}px)').matches;
  const rail = root.classList.contains('ma-rail')
    || (narrow && !root.classList.contains('ma-wide'));
  root.classList.toggle('ma-rail', !rail);
  root.classList.toggle('ma-wide', rail);
  try {{ localStorage.setItem('{RAIL_KEY}', rail ? 'wide' : 'rail'); }} catch (err) {{}}
}}"""


def rail_css(scope):
    """The sidebar as a rail of icons, written once and emitted for both ways in.

    `scope` prefixes every selector: the 접기 button writes a class on <html>, the
    breakpoint matches the window, and a rail that looked different depending on which
    one collapsed it would be two rails to keep in step. A plain .format(), not an
    f-string, because almost every brace here is CSS.
    """
    return """
{scope} .ma-side {{ width:{rail}px; padding:14px 8px 18px; overflow:visible; }}
/* Collapsed to nothing, never display:none: a width cannot animate away from a box
   that stopped existing, and the fold is meant to read as one movement. */
{scope} .ma-side__words, {scope} .ma-side__label {{ max-width:0; opacity:0; }}
/* Centred with padding rather than justify-content, for the same reason: the icon
   then travels to the middle of the rail as the rail narrows, instead of jumping
   there on the first frame and waiting for the width to catch up. */
{scope} .ma-side__item {{ padding:8px 0 8px {icon}px; }}
/* The mark and the 펼치기 button take the same square, and the hover swaps them: a
   rail has one row's width at the top and the logo and the way out of the rail both
   want it. Anything narrower than a press is a press people miss. */
{scope} .ma-side__top {{ position:relative; padding:4px 0 10px {mark}px; }}
{scope} .ma-side__fold {{
  position:absolute; left:50%; top:1px; transform:translateX(-50%);
  opacity:0; pointer-events:none;
}}
{scope} .ma-side__top:hover .ma-side__brand {{ opacity:0; }}
{scope} .ma-side__top:hover .ma-side__fold {{ opacity:1; pointer-events:auto; }}
/* The heading has no room for its word, but the grouping it marks is still worth a
   line: four questions read as four, where nine bare icons read as nine. */
/* The heading rolls up into its own rule: the height shrinks, the text is clipped
   away by the overflow it already has, and the border is the line that is left. */
{scope} .ma-side__group {{
  height:1px; padding:0; margin:9px 12px 7px; color:transparent;
  border-top-color:var(--line);
}}
/* The count stays a count: 아이콘만 was never 'and no number'. It rides on the icon's
   shoulder, because there is no row left for it to sit at the end of. */
/* Absolute in both widths (see .ma-badge), so the count slides up onto the icon's
   shoulder with the row rather than teleporting there on the first frame. */
{scope} .ma-badge {{
  top:2px; right:4px; padding:0 3px;
  min-width:15px; height:15px; line-height:13px; font-size:9.5px;
  background:var(--brand); border-color:var(--card); color:#fff;
}}
{scope} .ma-side__item.is-live .ma-badge {{ border-color:var(--brand-soft); }}
/* The name is gone from the row, so the row grows the page's own tooltip rather than
   a native title — which waits a second, wraps where it likes and wears the OS ink.
   .ma-side is overflow:visible above for this line alone. */
{scope} .ma-side__item::after {{
  content:attr(data-name); position:absolute; left:calc(100% + 8px); top:50%;
  transform:translateY(-50%); z-index:40; pointer-events:none; opacity:0;
  background:var(--ink); color:#fff; border-radius:7px; padding:4px 9px;
  font-size:11.5px; font-weight:600; white-space:nowrap; box-shadow:var(--shadow);
  transition:opacity .12s ease;
}}
{scope} .ma-side__item:hover::after {{ opacity:1; }}
{scope} .ma-side__fold .q-icon {{ transform:rotate(180deg); }}
""".format(scope=scope, rail=SIDE_RAIL,
           # Centred by arithmetic, because that is what a length can animate to:
           # the rail's own padding is 8px a side, which leaves this much beside it.
           icon=(SIDE_RAIL - 16 - 18) // 2, mark=(SIDE_RAIL - 16 - 28) // 2)


# One stylesheet, injected once per page by shell(). Everything below is a token or a
# component class; a page that reaches for .style() again is usually asking for one
# of these instead.
THEME = f'''
{FONT_PRELOAD}
<style>
@font-face {{
  font-family:'Pretendard Variable';
  font-weight:45 920;
  font-style:normal;
  font-display:swap;
  src:url('/vendor/pretendard/{FONT_FILE}') format('woff2-variations');
}}
:root {{
  --ink:{INK}; --subtle:{SUBTLE}; --muted:{MUTED};
  --bg:{BG}; --card:{CARD}; --sunken:{SUNKEN};
  --line:{LINE}; --hair:{HAIR};
  --brand:{BRAND}; --brand-soft:{BRAND_SOFT};
  --urgent:{css_color(URGENT)}; --soon:{css_color(SOON)}; --ok:{OK};
  --r:12px; --shadow:0 1px 2px rgba(24,24,27,.04), 0 1px 3px rgba(24,24,27,.06);
}}
body {{
  font-family:{FONT_STACK};
  background:var(--bg); color:var(--ink);
  -webkit-font-smoothing:antialiased;
  font-variant-numeric:tabular-nums;
}}
/* nicegui's page wrapper is a flex column with align-items:start, which
   shrink-wraps the header band to the width of the widest card. */
.nicegui-content {{
  padding:0 !important; gap:0 !important; align-items:stretch !important; width:100%;
}}
.ma-shell {{ min-height:100vh; width:100%; display:flex; align-items:stretch; }}

/* The sidebar is the navigation; the header band above the page is state. Keeping
   them apart is what let the header grow a logo, a status chip and an offer. */
.ma-side {{
  flex:none; width:{SIDE_WIDE}px; background:var(--card);
  border-right:1px solid var(--line);
  position:sticky; top:0; height:100vh; overflow-y:auto;
  display:flex; flex-direction:column; padding:14px 10px 18px;
  transition:width {SIDE_EASE}, padding {SIDE_EASE};
}}
/* 접기 sits on the sidebar it folds, not in the header band across the page: the
   button and the thing it moves are then one gesture, and the band keeps its width
   for what is actually state. */
.ma-side__top {{
  display:flex; align-items:center; gap:4px; padding:4px 4px 8px;
  transition:padding {SIDE_EASE};
}}
.ma-side__brand {{
  display:flex; align-items:center; gap:9px; flex:1; min-width:0;
  text-decoration:none; color:inherit; transition:opacity .12s ease;
}}
.ma-side__fold {{ flex:none; transition:opacity .12s ease; }}
.ma-side__mark {{
  display:grid; place-items:center; width:28px; height:28px; border-radius:8px;
  background:var(--brand); color:#fff; flex:none;
}}
.ma-side__mark svg {{ width:16px; height:16px; display:block; }}
/* max-width and not display: the name has to shrink with the rail rather than
   vanish under it, and overflow is what keeps it from spilling on the way. */
.ma-side__words {{
  display:flex; flex-direction:column; min-width:0; max-width:160px; overflow:hidden;
  transition:max-width {SIDE_EASE}, opacity .14s ease;
}}
.ma-side__name {{
  font-size:13.5px; font-weight:700; letter-spacing:-.01em; white-space:nowrap;
}}
.ma-side__ver {{ font-size:10.5px; color:var(--muted); }}
/* A pinned height, because the rail turns this heading into a 1px rule and a height
   cannot animate away from auto. The border is transparent until it is the rule. */
.ma-side__group {{
  font-size:10.5px; font-weight:700; letter-spacing:.06em;
  color:var(--muted); padding:13px 10px 5px;
  height:{SIDE_GROUP}px; box-sizing:border-box; overflow:hidden;
  border-top:1px solid transparent;
  transition:height {SIDE_EASE}, padding {SIDE_EASE}, margin {SIDE_EASE},
             color .14s ease, border-top-color .14s ease;
}}
.ma-side__item {{
  position:relative; display:flex; align-items:center; gap:9px;
  padding:7px 10px; border-radius:9px; text-decoration:none;
  color:var(--subtle); font-size:13px; font-weight:500;
  transition:background .12s ease, color .12s ease, padding {SIDE_EASE};
}}
.ma-side__item:hover {{ background:var(--sunken); color:var(--ink); }}
.ma-side__item.is-live {{
  background:var(--brand-soft); color:var(--brand); font-weight:600;
}}
.ma-side__item .q-icon {{ font-size:18px; flex:none; }}
.ma-side__label {{
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:160px;
  transition:max-width {SIDE_EASE}, opacity .14s ease;
}}
/* Out of the flow in both widths: in the row it sits at the end and in the rail on
   the icon's shoulder, and only a box that is positioned either way can travel
   between the two. Nothing in PAGES is long enough to run under it. */
.ma-badge {{
  position:absolute; right:10px; top:7px;
  min-width:19px; height:19px; padding:0 6px;
  border-radius:999px; background:var(--sunken); border:1px solid var(--line);
  color:var(--muted); font-size:10.5px; font-weight:700; line-height:17px;
  text-align:center;
  transition:right {SIDE_EASE}, top {SIDE_EASE}, min-width {SIDE_EASE},
             height {SIDE_EASE}, padding {SIDE_EASE}, font-size {SIDE_EASE},
             line-height {SIDE_EASE}, background .14s ease, border-color .14s ease,
             color .14s ease;
}}
.ma-side__item.is-live .ma-badge {{
  background:var(--brand); border-color:var(--brand); color:#fff;
}}
/* Collapsed because somebody pressed 접기, and collapsed because the window cannot
   hold the labels, are the same sidebar — rail_css() is what keeps them one. */
{rail_css('html.ma-rail')}
@media (max-width:{SIDE_BREAK - 1}px) {{
{rail_css('html:not(.ma-wide)')}
}}

/* min-width:0 and not flex:1 alone: a flex child's default minimum is its content,
   and the 메일 table would push the sidebar off the screen rather than scroll. And a
   block, never a flex column: .ma-page centres itself with margin:0 auto, and an auto
   cross-axis margin on a flex item overrides the stretch and shrink-wraps the page —
   which reads as a window that is half empty on both sides. */
.ma-main {{ flex:1; min-width:0; }}
.ma-bar {{
  position:sticky; top:0; z-index:20;
  background:rgba(255,255,255,.86); backdrop-filter:blur(10px);
  border-bottom:1px solid var(--line);
}}
.ma-bar__inner {{
  max-width:1240px; margin:0 auto; padding:0 20px;
  display:flex; align-items:center; gap:10px; height:56px;
}}
.ma-bar__title {{ font-size:14px; font-weight:700; letter-spacing:-.01em; }}
.ma-chip {{
  display:flex; align-items:center; gap:6px; flex:none;
  border:1px solid var(--line); border-radius:999px; padding:3px 11px 3px 9px;
  background:var(--card); font-size:11.5px; color:var(--muted);
}}
.ma-chip__dot {{ width:7px; height:7px; border-radius:50%; flex:none; }}
.ma-chip.is-live .ma-chip__dot {{ animation:ma-beat 1.7s ease-in-out infinite; }}
@keyframes ma-beat {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.3; }} }}
.ma-pill {{
  display:flex; align-items:center; gap:5px; flex:none; text-decoration:none;
  border-radius:999px; padding:4px 11px; font-size:11.5px; font-weight:600;
  background:var(--brand-soft); color:var(--brand);
}}
.ma-pill:hover {{ background:#dce7fb; }}
.ma-pill .q-icon {{ font-size:14px; }}
.ma-page {{ max-width:1240px; margin:0 auto; padding:20px 20px 56px; }}
.ma-lede {{ color:var(--muted); font-size:12.5px; line-height:1.6; margin-bottom:14px; }}

.ma-card {{
  display:block; background:var(--card); border:1px solid var(--line);
  border-radius:var(--r); box-shadow:var(--shadow); padding:16px 18px;
}}
.ma-card--flush {{ padding:0; overflow:hidden; }}
.ma-head {{
  display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:12px;
}}
.ma-head__title {{ font-size:13.5px; font-weight:700; letter-spacing:-.01em; }}
.ma-grid {{ display:grid; gap:14px; }}
.ma-stack {{ display:grid; gap:14px; align-content:start; }}
.ma-split {{
  display:grid; gap:14px; align-items:start;
  grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);
}}
@media (max-width:1080px) {{ .ma-split {{ grid-template-columns:minmax(0,1fr); }} }}
.ma-seg {{
  background:var(--sunken); border:1px solid var(--line);
  border-radius:9px; padding:2px; box-shadow:none;
}}
.ma-seg .q-btn {{
  font-size:11.5px; min-height:24px; padding:0 9px; border-radius:7px; font-weight:600;
}}
.ma-seg .q-btn__content {{ color:var(--muted); }}
.ma-seg .q-btn.bg-primary .q-btn__content {{ color:#fff; }}
.ma-sunken {{
  display:block; background:var(--sunken); border:1px solid var(--hair);
  border-radius:10px; padding:12px 14px;
}}

.ma-kpi {{
  display:flex; align-items:flex-start; gap:12px;
  background:var(--card); border:1px solid var(--line); border-radius:var(--r);
  box-shadow:var(--shadow); padding:14px 16px; text-decoration:none; color:inherit;
  transition:box-shadow .14s ease, transform .14s ease, border-color .14s ease;
}}
a.ma-kpi:hover {{
  box-shadow:0 4px 10px rgba(24,24,27,.07); transform:translateY(-1px);
  border-color:#d7d8dc;
}}
.ma-kpi__icon {{
  display:grid; place-items:center; width:34px; height:34px; border-radius:9px;
  background:var(--sunken); color:var(--muted); flex:none;
}}
.ma-kpi__icon .q-icon {{ font-size:19px; }}
.ma-kpi__label {{ font-size:12px; color:var(--muted); }}
.ma-kpi__value {{ font-size:25px; font-weight:700; line-height:1.2; letter-spacing:-.02em; }}
.ma-kpi__hint {{ font-size:11px; color:var(--muted); }}

/* 오늘의 AI 브리핑. The one card on the 대시보드 that is written rather than counted,
   so it is the one card that gets a moving edge — a beam everywhere is a beam nowhere,
   the same arithmetic as 분석 결과's two accents. It is a conic gradient turned by an
   animated custom property and masked down to the 1px frame: no extra element, nothing
   added to the layout, and the grey border underneath shows through wherever the
   gradient is transparent. @property is what makes an angle animate at all — without
   the registration the ring simply sits still, which is the fallback, not a break. */
@property --ma-angle {{ syntax:'<angle>'; initial-value:0deg; inherits:false; }}
.ma-beam {{
  position:relative;
  background:radial-gradient(130% 150% at 0% 0%, var(--brand-soft), transparent 58%),
             var(--card);
}}
.ma-beam::before {{
  content:''; position:absolute; inset:-1px; border-radius:calc(var(--r) + 1px);
  padding:1px; pointer-events:none;
  background:conic-gradient(from var(--ma-angle), transparent 0 56%,
             var(--brand-soft) 70%, var(--brand) 84%, transparent 93% 100%);
  -webkit-mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;
  mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite:exclude;
  animation:ma-beam 5s linear infinite;
}}
@keyframes ma-beam {{ to {{ --ma-angle:360deg; }} }}
/* A card that never stops moving is a card a reader learns to look away from. */
@media (prefers-reduced-motion:reduce) {{
  .ma-beam::before {{ animation:none; background:var(--brand-soft); }}
}}
.ma-brief__lede {{
  display:block; font-size:15px; font-weight:650; line-height:1.55;
  letter-spacing:-.012em; color:var(--ink);
}}
.ma-brief__grid {{
  display:grid; gap:11px; margin-top:12px;
  grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
}}
.ma-brief__sec {{
  display:block; background:var(--card); border:1px solid var(--hair);
  border-radius:10px; padding:10px 13px;
}}
/* Four headings in one blue read as one list broken into columns. The icon and the
   hue are the section's own, taken by position (BRIEF_MARKS) because Codex names its
   own sections — 우선 확인 beside 오늘 할 일 beside 다가오는 일정 is three questions,
   and they should not have to be read to be told apart. */
.ma-brief__head {{ display:flex; align-items:center; gap:5px; margin-bottom:5px; }}
.ma-brief__title {{ font-size:11.5px; font-weight:700; color:var(--sec,var(--brand)); }}
.ma-brief__line {{
  display:flex; gap:7px; align-items:baseline;
  font-size:12.5px; line-height:1.68; color:var(--ink);
}}
.ma-brief__line::before {{
  content:''; flex:none; width:4px; height:4px; border-radius:50%;
  background:#c6c8ce; transform:translateY(-3px);
}}
.ma-brief__watch {{
  display:flex; gap:7px; flex-wrap:wrap; align-items:center;
  margin-top:12px; padding-top:10px; border-top:1px solid var(--hair);
}}
.ma-brief__pin {{
  display:inline-flex; align-items:center; gap:5px; max-width:100%;
  text-decoration:none; border:1px solid var(--line); border-radius:999px;
  padding:3px 11px 3px 9px; font-size:11.5px; color:var(--subtle);
  background:var(--card); transition:border-color .12s ease, color .12s ease;
}}
.ma-brief__pin:hover {{ border-color:var(--brand); color:var(--brand); }}
.ma-brief__pin .q-icon {{ font-size:13px; flex:none; }}
/* ui.label is a div; the q-tooltip beside it is a q-tooltip, so > div is the text. */
.ma-brief__pin > div {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}

/* The hover card a mail carries, on the 브리핑's pins and on the 메일 목록's own
   button. The same slate as the 일정 tooltip and the chart tooltips, so a hover is
   recognisably one gesture across the app; .q-tooltip is in the selector because
   Quasar's own grey would otherwise win on equal specificity. */
.q-tooltip.ma-hint {{
  background:#27272a; color:#fafafa; border-radius:9px; padding:9px 11px;
  max-width:360px; font-size:12px; line-height:1.6; letter-spacing:-.005em;
  box-shadow:0 8px 24px rgba(24,24,27,.24); font-family:{FONT_STACK};
}}
.ma-hint__title {{
  font-weight:700; font-size:12.5px; margin-bottom:2px; overflow-wrap:anywhere;
}}
.ma-hint__row {{ color:#d4d4d8; overflow-wrap:anywhere; }}
.ma-hint__bad {{ color:#fca5a5; }}
.ma-hint__why {{ color:#a1a1aa; font-size:11.5px; margin-top:5px; overflow-wrap:anywhere; }}

.ma-meta {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.ma-meta__item {{ font-size:12px; color:var(--muted); }}
.ma-dot {{ width:7px; height:7px; border-radius:50%; flex:none; }}
.ma-empty {{
  color:var(--muted); font-size:12.5px; padding:18px 0; text-align:center;
}}
.ma-scroll {{ overflow-y:auto; }}
.ma-scroll::-webkit-scrollbar {{ width:9px; height:9px; }}
.ma-scroll::-webkit-scrollbar-thumb {{ background:#d9dade; border-radius:9px; }}
.ma-scroll::-webkit-scrollbar-track {{ background:transparent; }}
.ma-log {{
  font-size:11.5px; line-height:1.85; color:var(--subtle);
  font-variant-numeric:tabular-nums;
}}

/* 원문 속의 표. Not .ma-table: that one is the 메일 목록's, with a pointer cursor and
   a row hover, and nothing here is clickable. The mail decides how many columns it
   has, so the wrapper scrolls sideways rather than the panel doing it. */
.ma-sheet__wrap {{ display:block; overflow-x:auto; max-width:100%; margin:8px 0 10px; }}
.ma-sheet {{ border-collapse:collapse; font-size:12px; line-height:1.6; background:var(--card); }}
.ma-sheet td, .ma-sheet th {{
  border:1px solid var(--line); padding:5px 9px; text-align:left;
  vertical-align:top; color:var(--ink); white-space:nowrap;
}}
.ma-sheet th {{ background:var(--sunken); color:var(--muted); font-weight:600; }}

/* Quasar overrides: the table is the one place the defaults fight the page. */
.ma-table thead tr th {{
  background:var(--sunken); color:var(--muted); font-size:11.5px; font-weight:600;
  border-bottom:1px solid var(--line); position:sticky; top:0; z-index:1;
}}
/* A sortable header. q-table's own sort would only reorder the page it was handed,
   so the click goes back to sqlite and the arrow here is what that query decided. */
.ma-table thead tr th.ma-th {{ cursor:pointer; user-select:none; white-space:nowrap; }}
.ma-table thead tr th.ma-th:hover {{ color:var(--ink); background:#eef0f3; }}
.ma-table thead tr th.ma-th.is-live {{ color:var(--brand); }}
.ma-th__arrow {{ font-size:14px; margin-left:3px; vertical-align:-2px; opacity:0; }}
.ma-th:hover .ma-th__arrow {{ opacity:.45; }}
.ma-th.is-live .ma-th__arrow {{ opacity:1; }}
.ma-table tbody td {{ font-size:12.5px; border-bottom:1px solid var(--hair); }}
.ma-table tbody tr {{ cursor:pointer; }}
.ma-table tbody tr:hover {{ background:var(--sunken); }}
.ma-tag {{
  display:inline-flex; align-items:center; border-radius:6px; padding:1px 7px;
  font-size:11px; font-weight:600; line-height:1.75; white-space:nowrap;
}}
.ma-foot {{
  display:flex; gap:6px; align-items:center; flex-wrap:wrap;
  padding:9px 12px; border-top:1px solid var(--hair); background:var(--card);
}}
.ma-field {{
  display:block; font-size:11px; font-weight:600; color:var(--muted);
  margin:12px 0 3px; letter-spacing:.01em;
}}
.ma-field:first-child {{ margin-top:0; }}
.ma-radio {{ display:block; margin:0 0 2px -6px; }}
.ma-radio .q-radio__label {{ font-size:13px; color:var(--ink); }}
/* The 모델 dropdown. Quasar sizes a select's text for a form somebody fills in all
   day; this one is read far more often than it is changed, so the closed control
   matches the card's own body text rather than shouting one size larger. */
.ma-select .q-field__native {{ font-size:13px; color:var(--ink); font-weight:600; }}
.ma-alert {{
  display:flex; gap:7px; align-items:flex-start; border-radius:9px; padding:9px 11px;
  color:var(--urgent); background:rgba(192,0,0,.06); border:1px solid rgba(192,0,0,.14);
}}
.ma-alert--warn {{
  color:var(--soon); background:rgba(208,112,0,.07); border-color:rgba(208,112,0,.16);
}}
/* 분석 결과: one block per part, so 요약 and 다음 행동 are two things and not a
   wall. The left rule is the accent, and only the parts a reader has to act on get
   one — four colours would be none. */
.ma-part {{
  display:block; background:var(--card); border:1px solid var(--hair);
  border-left:3px solid var(--line); border-radius:10px;
  padding:10px 13px; margin-bottom:9px;
}}
.ma-part--brand {{ border-left-color:var(--brand); }}
.ma-part--ok {{ border-left-color:var(--ok); }}
.ma-part__head {{ display:flex; align-items:center; gap:6px; margin-bottom:5px; }}
.ma-part__head .q-icon {{ font-size:15px; flex:none; }}
.ma-part__label {{ font-size:11.5px; font-weight:700; color:var(--ink); }}
.ma-part__body {{
  display:block; font-size:13px; line-height:1.68; color:var(--ink);
  white-space:pre-wrap; overflow-wrap:anywhere;
}}
.ma-part__foot {{
  display:flex; align-items:center; gap:8px; flex-wrap:wrap;
  margin-top:7px; padding-top:6px; border-top:1px solid var(--hair);
}}
.ma-part__link {{ font-size:12px; color:var(--brand); text-decoration:none; }}
.ma-part__link:hover {{ text-decoration:underline; }}

.ma-row {{
  display:flex; gap:9px; align-items:baseline; flex-wrap:wrap;
  padding:6px 0; border-bottom:1px solid var(--hair);
}}
.ma-row:last-child {{ border-bottom:none; }}

/* 대시보드 마감: a checklist, so a deadline that is done can still be seen being done. */
.ma-due__head {{ display:flex; gap:10px; align-items:baseline; padding:0 18px 5px; }}
.ma-progress {{
  color:var(--brand); border-radius:999px; margin:0 18px 4px !important;
  width:auto !important;
}}
/* The same bar inside a card that already has its own padding — the 18px above is
   for a flush card, where the bar has to keep clear of the edge itself. */
.ma-progress--inline {{ margin:8px 0 4px !important; width:100% !important; }}
.ma-duelist {{ display:block; padding-bottom:8px; }}
/* 할 일 tally: three numbers wide enough to read at a glance from across the page. */
.ma-tally {{ display:flex; gap:0; padding:12px 18px 10px; }}
.ma-tally__cell {{ flex:1 1 0; display:grid; gap:2px; min-width:0; }}
.ma-tally__head {{ display:flex; align-items:center; gap:6px; }}
.ma-tally__n {{ font-size:21px; font-weight:700; line-height:1.15; letter-spacing:-.02em; }}
.ma-due {{
  display:flex; gap:8px; align-items:center; flex-wrap:nowrap;
  padding:2px 18px 2px 12px; border-bottom:1px solid var(--hair);
}}
.ma-due:last-child {{ border-bottom:none; }}
.ma-due:hover {{ background:var(--sunken); }}
.ma-due__day {{ font-size:12px; color:var(--subtle); flex:none; min-width:76px; }}
.ma-due__title {{
  font-size:12.5px; color:var(--ink); font-weight:500; text-decoration:none;
  min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}}
.ma-due__title:hover {{ color:var(--brand); text-decoration:underline; }}
.ma-due__left {{ font-size:12px; color:var(--muted); flex:none; }}
/* The text already says 지남; the colour only makes it findable at a glance. */
.ma-due__left.is-missed {{ color:var(--urgent); font-weight:600; }}
.ma-due.is-done .ma-due__day, .ma-due.is-done .ma-due__left {{ color:var(--muted); }}
.ma-due.is-done .ma-due__title {{ color:var(--muted); text-decoration:line-through; }}
/* Quasar's checkbox takes a palette name, not a colour, and its blue is not ours. */
.ma-due .q-checkbox__inner {{ color:var(--muted); }}
.ma-due .q-checkbox__inner--truthy {{ color:var(--brand); }}

/* 할 일: three lanes, each a card whose body scrolls on its own. */
.ma-lane__head {{
  display:flex; align-items:center; gap:8px;
  padding:12px 14px; border-bottom:1px solid var(--hair);
}}
.ma-count {{
  font-size:11px; font-weight:700; color:var(--muted); background:var(--sunken);
  border-radius:999px; padding:1px 8px;
}}
.ma-lane {{
  padding:10px 12px; display:grid; gap:8px; align-content:start;
  transition:background .14s ease;
}}
.ma-note {{
  display:block; position:relative; cursor:grab;
  background:var(--card); border:1px solid var(--line);
  border-left:3px solid var(--line);
  border-radius:10px; padding:10px 12px;
  transition:box-shadow .14s ease, border-color .14s ease, opacity .14s ease;
}}
.ma-note:hover {{ box-shadow:var(--shadow); border-color:#d7d8dc; }}
.ma-note:active {{ cursor:grabbing; }}
.ma-note__foot {{
  display:flex; align-items:center; gap:2px; margin-top:6px;
  padding-top:6px; border-top:1px solid var(--hair);
}}
/* Dragging a card. Only the lane it would land in lights up — a card that also
   changed colour under the cursor read as two things moving at once. */
.ma-note.is-dragging {{ opacity:.35; }}
.ma-note__grip {{
  position:absolute; top:7px; right:7px; color:var(--muted);
  opacity:0; transition:opacity .14s ease;
}}
.ma-note:hover .ma-note__grip {{ opacity:.5; }}
.ma-note__text {{ padding-right:18px; }}
.ma-card.is-over {{ border-color:var(--brand); box-shadow:0 0 0 3px var(--brand-soft); }}
.ma-card.is-over .ma-lane {{ background:var(--brand-soft); }}
.ma-drop {{
  border:1px dashed var(--line); border-radius:10px; padding:17px 0;
  text-align:center; color:var(--muted); font-size:12.5px;
  transition:border-color .14s ease, color .14s ease;
}}
.ma-card.is-over .ma-drop {{ border-color:var(--brand); color:var(--brand); }}

/* 메모: paper, and nothing else. A memo has no title, no date field and no state, so
   the coloured ground and the 3px edge are its entire structure — which is why the
   grounds in NOTE_COLORS are washed out rather than Windows' own sticker colours.
   The wall is a grid with align-items:start and not CSS columns: a memo's textarea
   grows as it is typed into, and with columns that growth pushes the last card of a
   column into the next one — the layout moving under the person writing, on the one
   screen that is nothing but text they are in the middle of. A grid leaves gaps
   instead, which is the trade the kanban lanes already make. */
.ma-memo {{
  display:block; position:relative; border-radius:10px; padding:10px 12px 6px;
  border:1px solid transparent; border-left:3px solid transparent;
  transition:box-shadow .14s ease;
}}
.ma-memo:hover {{
  box-shadow:0 2px 4px rgba(24,24,27,.05), 0 6px 16px rgba(24,24,27,.08);
}}
.ma-memo__body {{
  display:block; color:var(--ink); font-size:13px; line-height:1.7;
  white-space:pre-wrap; overflow-wrap:anywhere;
}}
/* Quasar's borderless input still brings a control box, a bottom slot for the hint
   that is not there, and a white ground — all of which sit on top of the paper. */
.ma-memo .q-field__control {{
  background:transparent; padding:0; min-height:0;
}}
.ma-memo .q-field__control:before, .ma-memo .q-field__control:after {{ display:none; }}
.ma-memo .q-field__bottom {{ display:none; }}
.ma-memo .q-field__native {{
  color:var(--ink); font-size:13px; line-height:1.7; padding:0; min-height:0;
  overflow-wrap:anywhere;
}}
.ma-memo .q-field__native::placeholder {{ color:rgba(24,24,27,.38); }}
.ma-memo__foot {{
  display:flex; align-items:center; gap:3px; min-height:26px;
  margin-top:5px; padding-top:5px; border-top:1px solid rgba(24,24,27,.07);
}}
.ma-memo__when {{
  font-size:10.5px; color:rgba(24,24,27,.46); flex:none;
  font-variant-numeric:tabular-nums;
}}
/* Revealed on hover, like the kanban card's grip: six swatches and two buttons on
   every card at rest would be more chrome than memo. focus-within keeps them up for
   a keyboard, which never hovers anything. */
.ma-memo__acts {{
  display:flex; align-items:center; gap:2px; margin-left:auto;
  opacity:0; transition:opacity .14s ease;
}}
.ma-memo:hover .ma-memo__acts, .ma-memo:focus-within .ma-memo__acts {{ opacity:1; }}
.ma-memo__pal {{ display:flex; align-items:center; gap:3px; margin-right:3px; }}
.ma-sw {{
  width:15px; height:15px; border-radius:50%; padding:0; cursor:pointer;
  border:1px solid rgba(24,24,27,.16);
}}
.ma-sw.is-live {{ box-shadow:0 0 0 1.5px #fff, 0 0 0 3px var(--ink); }}
/* The mail a memo belongs to — the one thing 할 일 cannot hold, so it is the one
   thing on the card besides the text itself. */
.ma-memo__link {{
  display:inline-flex; align-items:center; gap:5px; max-width:100%;
  background:rgba(255,255,255,.62); border:1px solid rgba(24,24,27,.08);
  border-radius:999px; padding:2px 9px 2px 7px; margin-top:7px;
  font-size:11px; color:var(--subtle); text-decoration:none; cursor:pointer;
}}
.ma-memo__link:hover {{ background:#fff; color:var(--ink); }}
.ma-memo__link .q-icon {{ font-size:13px; flex:none; opacity:.7; }}
.ma-memo__link span {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.ma-wall__head {{
  display:flex; align-items:center; gap:7px; margin:2px 0 9px;
  font-size:11px; font-weight:700; letter-spacing:.05em; color:var(--muted);
}}
.ma-wall__head .q-icon {{ font-size:14px; }}
.ma-wall__rule {{ flex:1 1 auto; height:1px; background:var(--line); }}

/* 초안 만들기: its own heading, its own fold, and the two pickers. The control sits
   on the block it opens for the reason 접기 sits on the sidebar — one gesture. */
.ma-maker {{
  display:block; background:var(--sunken); border:1px solid var(--hair);
  border-radius:10px; padding:8px 10px; margin-bottom:8px;
}}
.ma-maker__top {{ display:flex; align-items:center; gap:7px; }}
.ma-maker__title {{ font-size:12.5px; font-weight:700; color:var(--ink); }}
.ma-maker__row {{
  display:flex; align-items:center; gap:9px; flex-wrap:wrap; margin-top:8px;
}}
.ma-maker__label {{
  flex:none; width:30px; font-size:11px; font-weight:600; color:var(--muted);
}}

/* A block that opens and shuts in place. grid-template-rows and not height: a
   height cannot animate from auto, and the thing being folded here is a log whose
   length changes on every beat. The child carries the overflow, or the rows it is
   clipped to would be drawn over whatever is below. */
.ma-fold {{
  display:grid; grid-template-rows:0fr;
  transition:grid-template-rows {SIDE_EASE};
}}
.ma-fold.is-open {{ grid-template-rows:1fr; }}
.ma-fold > * {{ overflow:hidden; min-height:0; }}
.ma-fold__mark {{ transition:transform {SIDE_EASE}; }}
.ma-fold__mark.is-open {{ transform:rotate(180deg); }}

/* 상담: the two speakers differ by side and by ground, never by side alone.
   Quasar's q-chat-message brings its own palette and a little tail; the rules below
   are scoped to .ma-chat so nothing else on the site inherits the override. */
/* The thread takes whatever the frame has left, rather than a figure of its own:
   .ma-chatwrap is sized to the window below, and this is the one thing in the card
   that should grow. min-height keeps it a thread on a window too short to fill. */
.ma-chat {{ flex:1 1 auto; min-height:180px; width:100%; }}
/* nicegui pads the scroll content and lets it shrink-wrap, so on a narrow window the
   padding alone is wider than the area and the whole thread scrolls sideways.
   The right gutter clears Quasar's overlay thumb: a sent bubble is margin-left:auto
   and sat directly under it, so the scrollbar crossed my own message. */
.ma-chat .q-scrollarea__content {{ width:100%; padding:6px 20px 6px 14px; }}
.ma-chat .q-scrollarea__thumb {{ width:6px; border-radius:6px; opacity:.28; }}
.ma-chat .q-scrollarea__thumb:hover {{ opacity:.5; }}
.ma-chat .q-message {{ max-width:min(76%, 660px); margin-bottom:12px; }}
.ma-chat .q-message-sent {{ margin-left:auto; }}
.ma-chat .q-message-name {{ font-size:11px; color:var(--muted); margin-bottom:3px; }}
.ma-chat .q-message-stamp {{ font-size:10.5px; color:var(--muted); opacity:1; }}
.ma-chat .q-message-text {{
  background:var(--sunken); color:var(--ink); border:1px solid var(--hair);
  border-radius:12px; padding:8px 12px; font-size:13px; line-height:1.65;
  min-height:0; overflow-wrap:anywhere;
}}
.ma-chat .q-message-sent .q-message-text {{
  background:var(--brand-soft); border-color:#d8e3fb;
}}
/* The tail is drawn from the bubble's own colour and misses the border we added. */
.ma-chat .q-message-text:last-child:before {{ display:none; }}
.ma-chat .q-message-text-content {{ color:var(--ink); }}
.ma-wait {{ display:flex; align-items:center; gap:8px; }}
/* 답변 복사 rides under its own bubble rather than inside it: the bubble's content is
   what gets copied, and a button in there would be part of what a hand-selection
   takes. Quiet until the answer is hovered, because a thread of them is a column of
   buttons otherwise. */
/* width:100%, not display:block alone: nicegui's scroll content is a flex column
   with align-items:start, so a wrapper left to itself shrink-wraps and takes the
   bubble's own width down with it. */
.ma-said {{ display:block; width:100%; }}
.ma-said .q-message {{ margin-bottom:2px; }}
.ma-said__copy {{
  color:var(--muted); opacity:.4; margin:0 0 8px 2px;
  transition:opacity .12s ease, color .12s ease;
}}
.ma-said:hover .ma-said__copy, .ma-said__copy:focus {{ opacity:1; color:var(--brand); }}

/* 상담: the thread list beside the thread. One column on a narrow window, where a
   250px sidebar would leave the bubbles no room at all. */
/* Sized to the frame, not to a number: the window opens at webui.WINDOW and the
   card used to stop 200px short of the bottom of it. CHAT_CHROME is everything above
   and below this page — the header band and .ma-page's own padding — so the thread
   ends where the page does and nothing scrolls behind it. */
.ma-chatwrap {{
  display:flex; gap:14px; align-items:stretch; width:100%;
  height:calc(100vh - {CHAT_CHROME}px); min-height:420px;
}}
.ma-rooms {{ flex:0 0 252px; min-width:0; display:flex; flex-direction:column; padding:0; }}
.ma-thread {{ flex:1 1 auto; min-width:0; display:flex; flex-direction:column; }}
@media (max-width:900px) {{
  /* Stacked, the two cards are taller than any window: let the page scroll again. */
  .ma-chatwrap {{ flex-direction:column; height:auto; min-height:0; }}
  .ma-chat {{ height:min(58vh, 470px); flex:none; }}
  .ma-rooms {{ flex:none; width:100%; }}
  .ma-rooms__list {{ max-height:220px; }}
}}
.ma-rooms__head {{
  display:flex; align-items:center; gap:8px; padding:12px 12px 6px;
}}
.ma-rooms__find {{ padding:0 12px 8px; width:100%; }}
.ma-rooms__list {{ flex:1 1 auto; min-height:0; padding:0 6px 8px; }}
.ma-room {{
  border-radius:10px; padding:8px 10px; cursor:pointer; display:block;
  border:1px solid transparent;
}}
.ma-room:hover {{ background:var(--sunken); }}
.ma-room.is-open {{ background:var(--brand-soft); border-color:#d8e3fb; }}
.ma-room__top {{ display:flex; align-items:center; gap:6px; min-width:0; }}
.ma-room__icon {{ font-size:15px; color:var(--muted); flex:none; }}
.ma-room.is-open .ma-room__icon {{ color:var(--brand); }}
.ma-room__title {{
  font-size:12.5px; font-weight:600; color:var(--ink);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0;
}}
.ma-room__when {{ font-size:10.5px; color:var(--muted); flex:none; }}
.ma-room__last {{
  display:block; font-size:11.5px; color:var(--muted); margin-top:2px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}}

/* What rich_text() emits, and nothing else: no tables, no images, no links. */
.ma-chat p {{ margin:0 0 6px; }}
.ma-chat p:last-child {{ margin-bottom:0; }}
/* Tailwind's preflight strips list markers; a numbered answer needs its numbers. */
.ma-chat ul {{ list-style:disc outside; margin:0 0 6px; padding-left:19px; }}
.ma-chat ol {{ list-style:decimal outside; margin:0 0 6px; padding-left:21px; }}
.ma-chat li {{ margin:1px 0; }}
.ma-chat li::marker {{ color:var(--muted); }}
.ma-chat .ma-md__h {{ font-weight:700; margin:2px 0 4px; }}
.ma-chat code {{
  background:rgba(24,24,27,.06); border-radius:4px; padding:0 4px;
  font-size:12px; font-family:{MONO};
}}
.ma-chat pre {{
  background:rgba(24,24,27,.055); border-radius:8px; padding:8px 10px;
  margin:0 0 6px; overflow-x:auto; font-size:12px; line-height:1.6;
  font-family:{MONO};
}}
.ma-chat pre code {{ background:none; padding:0; font-size:inherit; }}
.ma-compose {{ display:flex; gap:8px; align-items:flex-end; margin-top:12px; }}

/* 대시보드 오늘 일정: the same row shape as 마감, one line each, kind by colour. */
.ma-today {{ display:block; padding:2px 0 6px; }}
.ma-today__row {{
  display:flex; gap:9px; align-items:center; flex-wrap:nowrap;
  padding:5px 18px; border-bottom:1px solid var(--hair);
}}
.ma-today__row:last-child {{ border-bottom:none; }}
.ma-today__row:hover {{ background:var(--sunken); }}
.ma-today__title {{
  font-size:12.5px; color:var(--ink); font-weight:500; text-decoration:none;
  min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}}
.ma-today__title:hover {{ color:var(--brand); text-decoration:underline; }}
.ma-today__kind {{ font-size:11.5px; color:var(--muted); flex:none; }}

/* The 일정 tooltip. Native title= waits a second, wraps where it likes and wears the
   desktop's own chrome; this is the same slate the chart tooltips use, so a hover on
   the calendar and a hover on a bar are recognisably the same gesture. */
.ma-tip {{
  position:fixed; z-index:9999; left:0; top:0; max-width:320px; pointer-events:none;
  background:#27272a; color:#fafafa; border-radius:9px; padding:9px 11px;
  font-size:12px; line-height:1.6; letter-spacing:-.005em;
  box-shadow:0 8px 24px rgba(24,24,27,.24);
  opacity:0; transform:translateY(3px); transition:opacity .12s ease, transform .12s ease;
  font-family:{FONT_STACK};
}}
.ma-tip.is-on {{ opacity:1; transform:translateY(0); }}
.ma-tip__head {{ display:flex; align-items:center; gap:6px; margin-bottom:3px; }}
.ma-tip__dot {{ width:7px; height:7px; border-radius:50%; flex:none; }}
.ma-tip__title {{ font-weight:700; font-size:12.5px; overflow-wrap:anywhere; }}
.ma-tip__row {{ color:#d4d4d8; overflow-wrap:anywhere; }}
.ma-tip__row b {{ color:#fafafa; font-weight:600; }}
.ma-tip__miss {{ color:#fca5a5; font-weight:600; }}
.ma-tip__hint {{ color:#a1a1aa; font-size:11px; margin-top:4px; }}

/* FullCalendar ships its own chrome; these lines make it this app's chrome. */
.fc {{
  --fc-border-color:{HAIR}; --fc-page-bg-color:{CARD};
  --fc-neutral-bg-color:{SUNKEN}; --fc-today-bg-color:{BRAND_SOFT};
  font-size:12.5px;
}}
.fc .fc-button {{
  background:{CARD}; border:1px solid {LINE}; color:{SUBTLE}; box-shadow:none;
  text-transform:none; font-size:12px; font-weight:600; padding:4px 11px;
  border-radius:8px;
}}
.fc .fc-button:hover {{ background:{SUNKEN}; color:{INK}; border-color:{LINE}; }}
.fc .fc-button-primary:not(:disabled).fc-button-active,
.fc .fc-button-primary:not(:disabled):active {{
  background:{BRAND_SOFT}; border-color:#d6e3fb; color:{BRAND}; box-shadow:none;
}}
.fc .fc-button-primary:focus {{ box-shadow:none; }}
.fc .fc-button:disabled {{
  background:{SUNKEN}; border-color:{HAIR}; color:{MUTED}; opacity:1;
}}
.fc .fc-col-header-cell-cushion {{
  color:{MUTED}; font-size:11.5px; font-weight:600; padding:8px 4px;
}}
.fc .fc-daygrid-day-number {{ color:{SUBTLE}; font-size:12px; padding:5px 7px; }}
.fc .fc-daygrid-event {{ border:none; border-radius:5px; padding:1px 5px; font-size:11.5px; }}
.fc .fc-list-day-cushion {{ background:{SUNKEN}; }}
.fc .fc-timegrid-event {{ border:none; border-radius:5px; padding:1px 4px; }}
.fc .fc-timegrid-event .fc-event-main {{ padding:0; }}
/* One row per event: icon, then time, then whatever room the title has left. */
.ma-ev {{ display:flex; align-items:center; gap:4px; min-width:0; overflow:hidden; }}
.ma-ev__icon {{ font-size:13px; line-height:1; flex:none; opacity:.92; }}
.ma-ev__time {{ font-weight:700; flex:none; font-variant-numeric:tabular-nums; }}
.ma-ev__title {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
/* In 주 the block is as tall as the event is long, so the title may wrap. */
.fc-timegrid-event .ma-ev {{ align-items:flex-start; flex-wrap:wrap; }}
.fc-timegrid-event .ma-ev__title {{ white-space:normal; overflow-wrap:anywhere; }}
.fc .fc-list-event-title .ma-ev__title {{ white-space:normal; }}
.fc .fc-timegrid-now-indicator-line {{ border-color:{css_color(URGENT)}; }}
.fc .fc-timegrid-now-indicator-arrow {{
  border-color:{css_color(URGENT)}; border-top-color:transparent; border-bottom-color:transparent;
}}
.fc-theme-standard td, .fc-theme-standard th {{ border-color:{HAIR}; }}
.q-field--outlined .q-field__control {{ border-radius:9px; }}
.q-btn {{ border-radius:9px; }}
/* Quasar sizes a dense button at 14px type in a 2.5em box, which stands a head taller
   than the 12.5px text it sits beside — a row of four of them read as the loudest
   thing on the page. Specificity is deliberately one class, so the segmented control
   (.ma-seg .q-btn) and the round icon buttons below keep their own sizes. */
.q-btn--dense {{ font-size:12.5px; min-height:27px; padding:1px 10px; font-weight:600; }}
.q-btn--dense .q-icon {{ font-size:15px; }}
.q-btn--dense.q-btn--round {{ min-height:26px; min-width:26px; padding:0; }}
/* The 메일 detail, opened over the list rather than under it: wide enough for the two
   columns and the draft, short enough that the page behind it still frames it. */
.ma-modal {{ width:min(1060px, 95vw); max-width:95vw; max-height:86vh; overflow-y:auto; }}
</style>
'''


# ---------------------------------------------------------------- shaping (no nicegui)

def bar_rows(pairs):
    """(name, value, percent of the largest) — scaled once, so the bars stay comparable."""
    top = max((value for _, value in pairs), default=0) or 1
    return [(name, value, round(100 * value / top)) for name, value in pairs]


def label_step(count, shown=10):
    """Print every nth date: 90 day labels in a row is a grey smear."""
    return max(1, -(-count // shown))


# containLabel reserves the axis gutter from ECharts' own estimate of the label
# height, and Pretendard's line box is taller than that estimate: without a few px
# here the date row is drawn half outside the canvas.
AXIS_GUTTER = 6


def axis_style(color=MUTED):
    """One axis look for every chart, so four charts on a page read as one instrument."""
    return {'axisLine': {'show': False}, 'axisTick': {'show': False},
            'axisLabel': {'color': color, 'fontSize': 11},
            'splitLine': {'show': False}}


def chart_base(left=8, right=16, top=16, bottom=8):
    """Grid, tooltip and font shared by every chart. containLabel keeps labels inside."""
    return {'grid': {'left': left, 'right': right, 'top': top, 'bottom': bottom,
                     'containLabel': True},
            'textStyle': {'fontFamily': CHART_FONT},
            'tooltip': {'trigger': 'axis', 'backgroundColor': '#27272a', 'borderWidth': 0,
                        'textStyle': {'color': '#fafafa', 'fontSize': 12},
                        'axisPointer': {'type': 'shadow',
                                        'shadowStyle': {'color': 'rgba(24,24,27,0.04)'}}},
            'animationDuration': 420}


def bar_option(pairs, tones=None):
    """Horizontal count bars. showBackground draws the empty track a 0 could not.

    triggerEvent is what makes the axis label clickable beside the bar: a 0 draws no
    bar at all, and 긴급 0 is exactly the row a reader wants to open — to see that it
    really is empty rather than to wonder whether the chart is stale. The background
    track is zrender's own and carries no event, so the label is the whole of it.
    """
    names = [name for name, _ in pairs]
    data = [{'value': value,
             'itemStyle': {'color': (tones or {}).get(name, SERIES), 'borderRadius': 4}}
            for name, value in pairs]
    option = dict(chart_base(left=0, right=34, top=6, bottom=0))
    option.update({
        'xAxis': dict(axis_style(), type='value', show=False),
        # ECharts draws a category axis bottom-up, so reverse to read top-down.
        'yAxis': dict(axis_style(INK), type='category', data=list(reversed(names)),
                      axisLabel={'color': INK, 'fontSize': 12}, triggerEvent=True),
        'series': [{'type': 'bar', 'data': list(reversed(data)), 'barWidth': 11,
                    'showBackground': True,
                    'backgroundStyle': {'color': HAIR, 'borderRadius': 4},
                    'label': {'show': True, 'position': 'right', 'formatter': '{c}',
                              'color': SUBTLE, 'fontSize': 11, 'fontWeight': 600}}],
    })
    return option


def trend_option(rows):
    """Collected, analysed and exported per day.

    Lines rather than bars: at 90 days three grouped bars a day are slivers, and the
    question the page asks — did the worker keep up — is a shape, not a value.
    """
    days = [day[5:] for day, _ in rows]
    # boundaryGap is off, so the first date sits on the axis origin and its centred
    # label hangs past it; containLabel only budgets for the y-axis on that side.
    option = dict(chart_base(left=14, right=18, top=28, bottom=AXIS_GUTTER))
    option.update({
        'legend': {'data': [label for _, label in TREND_LABELS], 'top': 0, 'right': 0,
                   'itemWidth': 10, 'itemHeight': 10, 'icon': 'roundRect',
                   'textStyle': {'color': MUTED, 'fontSize': 11}},
        'xAxis': dict(axis_style(), type='category', data=days, boundaryGap=False,
                      axisLabel={'color': MUTED, 'fontSize': 11,
                                 'interval': label_step(len(rows)) - 1}),
        'yAxis': dict(axis_style(), type='value', minInterval=1,
                      splitLine={'show': True, 'lineStyle': {'color': HAIR}}),
        'series': [{'name': label, 'type': 'line', 'smooth': False,
                    'data': [counts[key] for _, counts in rows],
                    'showSymbol': len(rows) <= 31, 'symbolSize': 5,
                    'lineStyle': {'width': 2, 'color': TREND_TONES[key]},
                    'itemStyle': {'color': TREND_TONES[key]},
                    'areaStyle': {'color': TREND_TONES[key], 'opacity': 0.08}}
                   for key, label in TREND_LABELS],
    })
    return option


def day_title(day):
    """'9월 14일 (월)'. Built by hand: Korean in a strftime format is a Windows crash."""
    return f'{day.month}월 {day.day}일 ({WEEKDAYS[day.weekday()]})'


def today_rows(events, today, handled=()):
    """오늘 날짜에 걸린 일정 — 시작도 마감도, 처리 완료된 메일만 빼고.

    The 마감 panel beside it answers '언제까지인가'; this one answers '오늘 무엇이
    있는가', which is why 시작 is kept here and dropped there.
    """
    return [{'kind': entry.kind, 'title': plain_title(entry.label), 'mail': entry.mail_id,
             'evidence': entry.evidence, 'manual': is_event_key(entry.mail_id)}
            for entry in events.get(today, []) if entry.mail_id not in handled]


def deadline_rows(data, today):
    """The 마감 checklist: missed first, then upcoming, in date order.

    A done row keeps its place but stops being a miss — the red is for what is still
    owed, and a deadline that was met late is not owed.

    The kind rides along so the row can carry the same icon the calendar draws: this
    panel sits directly under 오늘 일정, and the label's own marker arrived here in
    the link's colour, which said nothing about which kind it was.
    """
    return [{'day': day.isoformat(), 'title': plain_title(entry.label), 'kind': entry.kind,
             'left': describe(day, today), 'mail': entry.mail_id,
             'manual': is_event_key(entry.mail_id),
             'done': entry.mail_id in data['handled'],
             'missed': day < today and entry.mail_id not in data['handled']}
            for day, entry in data['due_window']]


def deadline_progress(rows):
    """How much of the window is finished. An empty list is not 100% done."""
    total = len(rows)
    done = sum(1 for row in rows if row['done'])
    return {'done': done, 'left': total - done, 'total': total,
            'ratio': done / total if total else 0.0,
            'percent': round(100 * done / total) if total else 0}


def summary_line(data):
    text = (f"수집 {data['total']}건 · 분석 대기 {data['waiting']}건"
            f" · 처리 완료 {len(data['handled'])}건")
    missed = len(data['past_due'])
    return text + (f' · 지난 마감 {missed}건' if missed else '')


BRIEF_SECTIONS = 4
BRIEF_LINES = 6
BRIEF_WATCH = 5
# A pin is a chip in a row of chips: a subject longer than this is a line of its own.
PIN_TITLE_MAX = 34
# The card's own reading order, drawn as an icon and a hue. Codex names its own
# sections — the schema constrains shape, not wording — so this rides on the position
# the prompt asks for (지난 마감·긴급 first, then 오늘, then what is coming) rather than
# on a title match, which a reworded heading would silently turn back into three
# identical blue headings. Three accents and a plain fourth: the last block is whatever
# was left over, and a colour on it would be a fourth priority nobody set.
BRIEF_MARKS = (('priority_high', 'urgent'), ('bolt', 'brand'),
               ('event', 'soon'), ('lightbulb', None))
BRIEF_TONES = {'urgent': css_color(URGENT), 'brand': BRAND, 'soon': css_color(SOON),
               None: MUTED}
BRIEF_TIP = ('오늘의 브리핑을 다시 만들도록 예약합니다. '
             '분석 대기가 없을 때 수집 차례에 만들어집니다.')


def briefing_view(row, today):
    """A stored briefing as the card draws it, or an empty one.

    The day it was written for travels with it rather than being checked away: a
    briefing from yesterday is still worth reading and is not today's, and a card that
    showed one without saying which day it covered would be the same silence
    '최신 버전입니다' behind a dead proxy was.
    """
    blank = {'has': False, 'stale': False, 'day': '', 'stamp': '',
             'headline': '', 'sections': [], 'watch': []}
    if row is None:
        return blank
    try:
        data = json.loads(row['result'])
    except (ValueError, TypeError):
        return blank
    day = str(row['day'] or '')
    try:
        written = date.fromisoformat(day)
    except ValueError:
        return blank
    clock = local_text(row['at'], '%H:%M')
    sections = []
    for part in (data.get('sections') or [])[:BRIEF_SECTIONS]:
        lines = [text.strip() for text in (part.get('lines') or []) if str(text).strip()]
        if lines:
            icon, accent = BRIEF_MARKS[len(sections)]
            sections.append({'title': str(part.get('title') or '').strip() or '메모',
                             'icon': icon, 'accent': accent, 'lines': lines[:BRIEF_LINES]})
    watch = [{'mail_id': pin.get('mail_id', ''),
              'reason': str(pin.get('reason') or '').strip() or '메일 열기'}
             for pin in (data.get('watch') or [])
             # A manual 일정 has no mail behind it, and a pin that opened an empty
             # 메일 화면 is worse than one that is simply not drawn.
             if pin.get('mail_id') and not is_event_key(pin.get('mail_id'))][:BRIEF_WATCH]
    return {'has': bool(sections or data.get('headline')), 'day': day,
            'stale': written != today,
            'stamp': f'{day_title(written)} {clock} 기준'.strip(),
            'headline': str(data.get('headline') or '').strip(),
            'sections': sections, 'watch': watch}


def watch_pins(watch, cards):
    """먼저 볼 메일, named after the mail rather than after the sentence about it.

    The pin used to draw Codex's `reason` — a line written about a mail, standing in
    a row of other lines about other mail, with nothing saying which was which. The
    name is the mail's own subject; the reason is what the hover is for. A mail that
    has been deleted since the briefing was written is not in `cards` and is dropped:
    a pin that opens an empty 메일 화면 is worse than one that is simply not drawn.
    """
    pins = []
    for pin in watch:
        row = cards.get(pin['mail_id'])
        if row is None:
            continue
        tip = mail_tip(row)
        pins.append({'mail_id': pin['mail_id'], 'title': clip(tip['subject'], PIN_TITLE_MAX),
                     'reason': pin['reason'], 'tip': tip})
    return pins


def briefing_card(opened, account, today):
    """The stored briefing with its pins named after the mail they point at.

    The two halves are pure and tested; this is the one line that needs the database,
    and it is one `IN (…)` for the whole card — the ids are not known until the stored
    JSON has been read, which is why it cannot be a parameter of briefing_view().
    """
    view = briefing_view(opened.briefing(account) if account else None, today)
    view['watch'] = watch_pins(view['watch'],
                               opened.mail_cards(pin['mail_id'] for pin in view['watch']))
    return view


def briefing_empty(running, total, hour):
    """Why the card has nothing in it. Never blank space with no reason beside it."""
    if not total:
        return '수집된 메일이 없어 아직 브리핑할 내용이 없습니다.'
    if not running:
        return '아직 브리핑이 없습니다. 수집을 시작하면 오늘 치를 만듭니다.'
    if hour < BRIEF_HOUR:
        return f'아직 브리핑이 없습니다. 오전 {BRIEF_HOUR}시 이후 첫 수집 때 만듭니다.'
    return '아직 브리핑이 없습니다. 분석 대기가 비면 다음 수집 차례에 만듭니다.'


def briefing_text(running):
    """다시 만들기 is a booking, exactly as 다시 분석 is: the worker does the work."""
    if running:
        return '브리핑을 다시 만들도록 요청했습니다. 다음 수집 차례에 반영됩니다.'
    return '브리핑을 다시 만들도록 예약했습니다. 수집을 시작하면 만들어집니다.'


def stale_text(view, today):
    """어제 브리핑 is a different sentence from 오늘 브리핑, and has to read as one."""
    try:
        gap = (today - date.fromisoformat(view['day'])).days
    except ValueError:
        return ''
    return f'{gap}일 전 브리핑입니다. 오늘 치는 다음 수집 차례에 만들어집니다.'


def run_summary(hub, directory, config, limit=RUN_LINES):
    """What the 실행 strip shows. Read-only: the hub owns the worker, not the screen."""
    state = hub.state()
    try:
        account = account_key(config)
    except (KeyError, AttributeError):
        account = ''
    stamps = {}
    if account:
        # The thread's own connection, not a new one: 실행 reads this every second now,
        # and a fresh Store runs migrate() and both backfills before it answers.
        opened = store(directory)
        for label, key in (('마지막 확인', 'last_fetch:'), ('마지막 반영', 'last_export:')):
            stamp = opened.get_meta(key + account)
            stamps[label] = local_text(stamp, '%m-%d %H:%M:%S') if stamp else '—'
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
    events = list(store(directory).events(account)) if account else []
    return overview(rows, today or date.today(), within=within, events=events)


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
    # '' is in neither set, which is the answer for anything else that arrives too.
    for key, _, names in BAR_FILTERS:
        if values[key] not in names:
            values[key] = ''
    values['query'] = str(values['query'] or '')
    values['desc'] = bool(values['desc'])
    try:
        values['page'] = max(0, int(values['page']))
        values['per'] = min(200, max(10, int(values['per'])))
    except (TypeError, ValueError):
        values['page'], values['per'] = 0, LIST_LIMIT
    return values


def clicked_key(args):
    """The column name a header click carried.

    A slot template reaches the server through $parent.$emit, and Element.on hands the
    handler the client's argument *list* — ['received'], not 'received'. A bare string
    is taken too, so the caller never has to know which shape arrived.
    """
    if isinstance(args, (list, tuple)):
        return str(args[0]) if args else ''
    return str(args or '')


# What a click on a count bar sends back. nicegui transmits only the fields the
# handler names, so this list is also what never leaves the browser.
BAR_EVENT = ('componentType', 'name', 'value')


def clicked_bar(args, names):
    """The row a 메일 종류·우선순위 chart click landed on, or '' for anything else.

    Two shapes arrive: a bar carries its category in `name`, an axis label carries it
    in `value` — and the *count* is in whichever field is left, so the answer is the
    one of the two that is a name this chart was drawn from. That test is the filter's
    validation as well: nothing that is not already a row on this page can come back.
    """
    if not isinstance(args, dict):
        return ''
    for key in ('name', 'value'):
        value = args.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ''
        if str(value) in names:
            return str(value)
    return ''


def next_sort(sort, desc, key):
    """(sort, desc) after a click on the `key` column's header.

    The same column flips; a new one opens in the direction that column is read —
    제목·발신자·종류 from ㄱ, 수신·우선순위·상태 newest and most urgent first. Starting
    every column descending made a click on 제목 look broken.
    """
    if key not in SORTS:
        return sort, desc
    if key == sort:
        return key, not desc
    return key, key not in TEXT_SORTS


# What a hover can hold before it stops being a hover. A 요약 runs to a paragraph and
# the tooltip is read standing up, over the row it belongs to.
TIP_TEXT = 180


def clip(text, limit):
    """One line, cut to `limit` with the ellipsis inside the count."""
    line = ' '.join(str(text or '').split())
    if len(line) <= limit:
        return line
    return line[:limit - 1].rstrip() + '…'


def mail_tip(row):
    """What a mail says on a hover: who sent it, how urgent, and what it asked for.

    One shape for both places that draw it — the 브리핑's 먼저 볼 메일 and the 메일
    목록's own button — so the same mail cannot describe itself two ways on one
    screen. Pure, and takes the list row the caller is already holding: a detail()
    per row per beat is exactly what this is instead of.
    """
    result = json.loads(row['result']) if row['result'] else {}
    return {'id': row['id'], 'subject': row['subject'] or '(제목 없음)',
            'sender': row['sender'] or '', 'received': local_text(row['received']),
            'category': result.get('category', ''), 'priority': result.get('priority', ''),
            'state': state_of(row),
            'summary': clip(result.get('summary', ''), TIP_TEXT),
            'action': clip(result.get('next_action', ''), TIP_TEXT),
            'error': clip(row['error'], TIP_TEXT)}


def listing(directory, config, state):
    """One page of the list, plus what the pager needs to describe it."""
    account = account_of(config)
    if not account:
        return {'rows': [], 'total': 0, 'page': 0, 'pages': 0, 'first': 0, 'last': 0}
    page, per = state['page'], state['per']
    narrow = {'query': state['query'], 'state': state['state'],
              'category': state['category'], 'priority': state['priority'],
              'sort': state['sort'], 'desc': state['desc'], 'limit': per}
    rows, total = store(directory).search(account, offset=page * per, **narrow)
    if not rows and total and page:
        # The page fell off the end, which a filter change does all the time.
        page = max(0, (total - 1) // per)
        rows, total = store(directory).search(account, offset=page * per, **narrow)
    # The tip rides on the row rather than being fetched when the button is hovered:
    # a q-table row is drawn in the browser, and asking the server per hover would be
    # a round trip for text the list already had in its hand.
    return {'rows': [{**row_view(row), 'tip': mail_tip(row)} for row in rows],
            'total': total, 'page': page,
            'pages': max(1, -(-total // per)), 'first': page * per + 1 if total else 0,
            'last': min(total, page * per + len(rows))}


def list_signature(data):
    """What a repaint of the 메일 목록 would actually change.

    The list polls every second and a rebuild is also what clears q-table's
    checkboxes, so it has to be able to tell 'the worker analysed something' from
    'nothing happened'. Every column the table draws is in here; `received` is not,
    because it is fixed once the mail is stored. The hover column is, through the two
    fields of it an analysis can rewrite on its own: a second answer that changed the
    요약 and left the 우선순위 where it was would otherwise leave a stale tooltip.
    """
    return (data['total'], data['page'],
            tuple((row['id'], row['state'], row['category'], row['priority'],
                   row['subject'], row['sender'],
                   row['tip']['summary'], row['tip']['action']) for row in data['rows']))


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


# A row of our own Markdown table, and the rule under its header. parse_mail writes
# both (core.table_text), so the shapes matched here are ones this app emitted.
CELL_SPLIT = re.compile(r'(?<!\\)\|')


def is_row(line):
    return line.startswith('|') and line.endswith('|') and len(line) > 1


def is_rule(line):
    cells = split_cells(line)
    return bool(cells) and all(cell == '---' for cell in cells)


def split_cells(line):
    """The cells of a Markdown row, with the escape core.squash() put on a pipe undone."""
    return [cell.strip().replace(r'\|', '|') for cell in CELL_SPLIT.split(line)[1:-1]]


def table_at(lines, index):
    """(rows, the line after) for a table starting here, or None.

    A header, the rule under it and at least one row: all three, because a mail may
    well write a line of its own that begins and ends with a pipe, and drawing a grid
    round that would be the parser inventing a table the sender never sent.
    """
    if index + 2 >= len(lines) or not is_row(lines[index]) or not is_rule(lines[index + 1]):
        return None
    rows, at = [split_cells(lines[index])], index + 2
    while at < len(lines) and is_row(lines[at]) and not is_rule(lines[at]):
        rows.append(split_cells(lines[at]))
        at += 1
    return (rows, at) if len(rows) > 1 else None


def body_blocks(text):
    """원문 as ('text', str) and ('table', rows) blocks, so a table is drawn as one.

    The mail's own HTML is never rendered: what is drawn back is the text parse_mail
    already made of it, which is why the panel needs no sanitising of its own.
    """
    blocks, plain, lines = [], [], [line.strip() for line in str(text or '').split('\n')]

    def flush():
        while plain and not plain[-1]:
            plain.pop()
        if plain:
            blocks.append(('text', '\n'.join(plain)))
        plain.clear()

    index = 0
    while index < len(lines):
        found = table_at(lines, index)
        if found:
            flush()
            blocks.append(('table', found[0]))
            index = found[1]
        else:
            if plain or lines[index]:
                plain.append(lines[index])
            index += 1
    flush()
    return blocks


def board(rows, todos):
    """{state: [card]} for the kanban. Mail-derived cards and manual ones look alike.

    The mail's own 처리 상태 is the card's column, so moving a card is the same act as
    marking the mail — there is no second source of truth to drift.
    """
    lanes = {state: [] for state, _ in COLUMNS}
    for row, result in results(rows):
        action = (result.get('next_action') or result.get('requests') or '').strip()
        # A mail card the user swept off the board: the mail itself is untouched, and
        # the 메일 화면 is where it can be put back. board_counts() reads this function,
        # so the 대시보드 tally drops the same card at the same moment.
        if not action or row['todo_hidden']:
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


def board_counts(rows, todos):
    """The kanban in three numbers, counted by board() itself.

    Going through board() rather than counting states in SQL is the point: the 대시보드
    block and the 할 일 판 then cannot disagree about what is a card — a mail with no
    next_action is not one, and no query knows that.
    """
    lanes = board(rows, todos)
    counts = {state: len(lanes[state]) for state, _ in COLUMNS}
    total = sum(counts.values())
    counts['total'] = total
    counts['ratio'] = counts[HANDLED] / total if total else 0.0
    return counts


def nav_counts(rows, todos, today=None):
    """What is left, beside 메일, 할 일 and 초안 in the sidebar.

    Through overview() and board_counts(), never through SQL of its own. A badge that
    counted `handled` itself would disagree with the 대시보드 card beside it, and no
    COUNT(*) knows that a mail whose analysis produced no next_action is not a 할 일
    card — which is the same reason board_counts() exists at all.
    """
    data = overview(rows, today or date.today())
    lanes = board_counts(rows, todos)
    return {'/mail': data['cards']['미처리 메일'],
            '/todo': lanes['total'] - lanes[HANDLED],
            '/drafts': data['cards']['검토 전 초안']}


def drag_payload(state, card_row):
    """What a dragged card carries. The id goes last, so one holding a colon survives."""
    return f"{card_row['kind']}:{state}:{card_row['key']}"


def drag_drop(payload, state):
    """(kind, id) for a card dropped into `state`, or None when it did not move.

    A card dropped back where it started is not a move: writing the state it already
    has would rebuild the whole board for nothing, which reads as a flicker.
    """
    kind, _, rest = str(payload or '').partition(':')
    origin, _, key = rest.partition(':')
    if kind not in ('mail', 'todo') or not key or origin == state:
        return None
    if kind == 'todo' and not key.isdigit():
        return None
    return kind, key


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


def note_tone(color):
    """(ground, edge) for a stored colour key, falling back to the default paper.

    A key kept for a colour that no longer exists reads as the default rather than
    raising, for the reason model_rows() keeps a row for a model Codex stopped
    listing: the value in the database was written by a version of this app, and the
    screen's job is to show it, not to argue with it.
    """
    for key, _, ground, edge in NOTE_COLORS:
        if key == color:
            return ground, edge
    return NOTE_COLORS[0][2], NOTE_COLORS[0][3]


def note_view(row, subjects=None):
    """One memo as the wall draws it. `subjects` is {mail id: subject}, read in one go."""
    subjects = subjects or {}
    mail_id = row['mail_id'] or ''
    ground, edge = note_tone(row['color'])
    return {'id': row['id'], 'text': row['text'] or '', 'color': row['color'] or '',
            'ground': ground, 'edge': edge, 'pinned': bool(row['pinned']),
            'mail': mail_id, 'subject': subjects.get(mail_id, ''),
            'when': local_text(row['updated'] or row['created'])}


def note_views(rows, subjects=None):
    return [note_view(row, subjects) for row in rows]


def keep_note(view, kind=''):
    if kind == 'pin':
        return view['pinned']
    if kind == 'mail':
        return bool(view['mail'])
    return True


def note_tally(views):
    """The number on each segment, from the same list the segments filter.

    Not `note_counts`: build() already has a local of that name feeding the sidebar
    cache, and a module function it shadows is a bug waiting for whoever calls it
    inside that scope.
    """
    return {kind: sum(1 for view in views if keep_note(view, kind))
            for kind, _ in NOTE_FILTERS}


def note_wall(views, kind=''):
    """{'pinned', 'rest'} — the two walls, with the filter already applied to both.

    The filter runs before the split rather than after, or '고정' would draw an empty
    lower wall beneath the pinned one and the page would look half broken.
    """
    shown = [view for view in views if keep_note(view, kind)]
    return {'pinned': [view for view in shown if view['pinned']],
            'rest': [view for view in shown if not view['pinned']]}


def note_signature(views):
    """What the wall actually draws, and nothing else.

    The 메모 화면 must never rebuild itself on a timer — every card on it is a
    textarea somebody may be inside, which is the edit-eating the 초안 화면 exists to
    stop. So the tick compares this and only *says* that something arrived; the text
    is deliberately in it, because another screen (the 메일 detail) writes memos too
    and a reader who cannot see that they changed is being lied to.
    """
    return tuple((view['id'], view['text'], view['color'], view['pinned'], view['mail'])
                 for view in views)


def pinned_notes(views, limit=PINNED_ON_HOME):
    """The 대시보드's few. Read-only there: the wall is where a memo is written."""
    return [view for view in views if view['pinned']][:limit]


BULLET = re.compile(r'^\s*[-*]\s+(.*)$')
NUMBERED = re.compile(r'^\s*\d+[.)]\s+(.*)$')
HEADING = re.compile(r'^\s*#{1,6}\s+(.*)$')
# One alternation, so a `**` inside a code span is code and not an unclosed bold.
INLINE = re.compile(r'`([^`]+)`|\*\*(.+?)\*\*')


def inline_html(line):
    """`code` and **bold**, on an already-escaped line."""
    return INLINE.sub(lambda hit: (f'<code>{hit.group(1)}</code>' if hit.group(1) is not None
                                   else f'<b>{hit.group(2)}</b>'), line)


def rich_text(text):
    """The little Markdown a Codex answer actually uses, as HTML.

    Forty lines rather than ui.markdown, which imports markdown2 and pygments — and
    pygments is 8.7MB of the installer that UI-PLAN 1단계 listed as removable precisely
    because nothing uses it (README calls the download size the user's first friction).
    Everything is escaped before a single tag is added, so the result is safe on its
    own; nicegui's client-side sanitiser is then a second lock, not the only one.
    """
    out, para, items, tag, fence = [], [], [], '', None

    def flush():
        if para:
            out.append('<p>' + '<br>'.join(para) + '</p>')
            para.clear()
        if items:
            out.append(f'<{tag}>' + ''.join(f'<li>{item}</li>' for item in items) + f'</{tag}>')
            items.clear()

    for raw in str(text or '').splitlines():
        if raw.lstrip().startswith('```'):
            if fence is None:
                flush()
                fence = []
            else:
                out.append('<pre><code>' + escape('\n'.join(fence)) + '</code></pre>')
                fence = None
            continue
        if fence is not None:
            fence.append(raw)
            continue
        line = inline_html(escape(raw.rstrip()))
        head = HEADING.match(line)
        bullet = BULLET.match(line) or NUMBERED.match(line)
        if not line.strip():
            flush()
        elif head:
            flush()
            out.append(f"<div class='ma-md__h'>{head.group(1)}</div>")
        elif bullet:
            wanted = 'ul' if BULLET.match(line) else 'ol'
            if items and tag != wanted:
                flush()
            tag = wanted
            if para:
                out.append('<p>' + '<br>'.join(para) + '</p>')
                para.clear()
            items.append(bullet.group(1))
        else:
            if items:
                flush()
            para.append(line)
    if fence is not None:              # an answer cut off mid-block still has to show
        out.append('<pre><code>' + escape('\n'.join(fence)) + '</code></pre>')
    flush()
    return ''.join(out)


def chat_context(row):
    """What a chat turn may see of a mail: the analysis and a trimmed body, no raw blob."""
    if row is None:
        return None
    view = detail_view(row)
    return {'subject': view['subject'], 'sender': view['sender'], 'received': view['received'],
            'category': view['category'], 'priority': view['priority'],
            'summary': view['summary'], 'requests': view['requests'],
            'events': view['events'], 'body': view['body'][:8000]}


# 상담 rooms. The general thread and every mail's thread already existed as keys in
# `chat.mail_id`; a free-standing room is the third kind and is the only one that has
# to carry a name of its own.
GENERAL_ROOM = '일반 상담'
NEW_ROOM = '새 대화'
# One icon per kind of thread, so the list says what a row is without a second word.
ROOM_ICONS = {'general': 'forum', 'mail': 'mark_email_read', 'room': 'chat_bubble_outline'}
ROOM_TITLE_MAX = 26
ROOM_PREVIEW_MAX = 46


def room_title(text):
    """A free room names itself after its first question, the way a chat list does.

    One line: a pasted question arrives with its newlines, and a title is one row in a
    narrow column.
    """
    line = ' '.join(str(text or '').split())
    if len(line) <= ROOM_TITLE_MAX:
        return line
    return line[:ROOM_TITLE_MAX - 1].rstrip() + '…'


def room_preview(row):
    """What was last said in a thread, and by whom. '' for a room nobody has used."""
    text = ' '.join(str(row.get('last') or '').split())
    if not text:
        return ''
    line = ('나: ' if row.get('role') == 'user' else 'Codex: ') + text
    if len(line) <= ROOM_PREVIEW_MAX:
        return line
    return line[:ROOM_PREVIEW_MAX - 1].rstrip() + '…'


def room_rows(rooms, open_key=''):
    """`Store.rooms()` as the sidebar draws it: kind, name, last line, when.

    Three kinds, because only one of them owns its name — a mail thread is called
    after the mail and dies with it, the general thread is always there and cannot be
    deleted, and only a 새 대화 can be renamed or thrown away.
    """
    shaped = []
    for row in rooms:
        key = str(row.get('key') or '')
        name = str(row.get('name') or '').strip()
        if not key:
            kind, title = 'general', GENERAL_ROOM
        elif key.startswith(ROOM_MARK):
            kind, title = 'room', name or NEW_ROOM
        else:
            kind, title = 'mail', name or '(제목 없음)'
        shaped.append({'key': key, 'kind': kind, 'title': title,
                       'preview': room_preview(row),
                       'when': local_text(row['at']) if row.get('at') else '',
                       'turns': int(row.get('turns') or 0), 'open': key == open_key})
    return shaped


def room_search(rows, query):
    """Filter the sidebar by title or by what was last said in the thread."""
    text = str(query or '').strip().lower()
    if not text:
        return rows
    return [row for row in rows
            if text in row['title'].lower() or text in row['preview'].lower()]


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


def collect_text(message, running):
    """What 지금 가져오기 reports. Collecting is not analysing.

    A one-off fetch stores what arrived and stops there — analysis is the worker's,
    and it can take minutes per mail. A screen that did not say so looked as though
    the mail had arrived broken.
    """
    message = str(message or '').strip() or '가져올 새 메일이 없습니다.'
    if running:
        return message
    return message + ' · 분석은 수집을 시작하면 진행됩니다.'


# 다시 분석 queues; the analysis itself is the worker's, exactly as collecting is.
# The tooltip says so unconditionally rather than only while the worker is stopped:
# it is built once and the worker can start or stop under it, and a tooltip that has
# to be right at two different moments is better off saying the thing that is always
# true.
REANALYZE_TIP = ('기존 분석 결과를 지우고 다시 분석하도록 예약합니다. '
                 '분석은 수집이 실행 중일 때 진행됩니다.')


def reanalyze_text(asked, wanted, running=True):
    """What 다시 분석 reports: what it skipped, and whether anything will act on it.

    Store.reset() skips a mail that is in Codex right now, because clearing its result
    would be overwritten by the answer already on its way. A button that then said
    '요청했습니다' would be describing something that did not happen.

    Nor does the button analyse anything itself — it clears the result and wakes the
    worker, and with the collector stopped there is no worker to wake. So it says so,
    the way collect_text() does: the request is real and keeps, but nothing moves
    until 실행 is started.
    """
    skipped = max(0, wanted - asked)
    if skipped and not asked:
        return ('지금 분석 중이라 다시 분석할 수 없습니다. '
                '분석이 끝나면 다시 눌러 주세요.')
    text = f'{asked}건을 다시 분석하도록 요청했습니다.'
    if skipped:
        text += f' {skipped}건은 지금 분석 중이라 건너뛰었습니다.'
    if not running:
        text += ' 분석은 수집을 시작하면 진행됩니다.'
    return text


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


# 분석 결과 was four label pairs in a column, which read as one wall of text: the
# headings were the only thing dividing 요약 from 다음 행동 and they were 11px grey.
# Each part is now its own block, and the accent is spent only on the two a reader has
# to *do* something about — colouring all four would be colouring none.
ANALYSIS_PARTS = (('summary', '요약', 'subject', None),
                  ('requests', '요청사항', 'assignment', 'brand'),
                  ('reason', '우선순위 근거', 'rule', None),
                  ('action', '다음 행동', 'bolt', 'ok'))


# The accent name each part carries, as a colour. None is the plain rule: a part
# nobody has to act on.
PART_TONES = {None: MUTED, 'brand': BRAND, 'ok': OK}


def analysis_blocks(view):
    """The 분석 결과 parts that have something in them, in reading order.

    Pure so the tests can hold the order and the accents without nicegui: the blocks
    are the screen's structure, and a part that silently stopped being drawn is
    exactly the kind of thing no screenshot review catches.
    """
    blocks = []
    for key, label, icon, accent in ANALYSIS_PARTS:
        text = str(view.get(key) or '').strip()
        if text:
            blocks.append({'key': key, 'label': label, 'icon': icon,
                           'accent': accent, 'text': text})
    return blocks


def event_kind(event):
    """마감·시작·확인 필요 for one analysed event, the way the calendar decides it.

    An event whose dates neither parse nor exist is 확인 필요 even when Codex did not
    say so: it is on no calendar, and the card is the only place that can say why.
    """
    dated = {name: parse_day(event.get(name))[0]
             for name in ('start', 'deadline')}
    if event.get('needs_review') or not any(dated.values()):
        return '확인 필요'
    return '마감' if dated['deadline'] else '시작'


def detail_events(events):
    """The mail's 일정 as the detail panel draws them, kind and all."""
    shaped = []
    for event in events:
        dated = [parse_day(event.get(name))[0] for name in ('start', 'deadline')]
        shaped.append({
            'title': str(event.get('title') or '(제목 없음)'),
            'kind': event_kind(event),
            'start': event_text(event.get('start')),
            'deadline': event_text(event.get('deadline')),
            'evidence': str(event.get('evidence') or ''),
            # What the panel can say and the calendar cannot: this one is not there.
            'on_calendar': any(dated),
        })
    return shaped


def draft_picks(saved=None):
    """마지막에 고른 말투와 방향, 또는 기본값. A value this build no longer offers
    falls back rather than being drawn as a toggle with nothing selected."""
    saved = saved or {}
    return {'tone': saved.get('tone') if saved.get('tone') in DRAFT_TONES else DRAFT_TONES[0],
            'way': saved.get('way') if saved.get('way') in DRAFT_WAYS else DRAFT_WAYS[0]}


def draft_input(view):
    """초안 만들기에 보내는 것: 메일 자체와, 분석이 이미 읽어 둔 것.

    The body goes in as it was written, not as its 한글 번역: the reply is meant to be
    in the sender's own language, and a translation is one more thing between the two.
    """
    return {'subject': view['subject'], 'sender': view['sender'],
            'received': view['received'], 'body': view['body'],
            'summary': view['summary'], 'requests': view['requests'],
            'action': view['action']}


def row_value(row, key, fallback=''):
    """A column that a row may not carry, for the shaping that runs off a fake row.

    Store.migrate() adds every column the app needs, so a real row has them all; the
    tests and the Excel side build rows of their own and must not have to.
    """
    try:
        value = row[key]
    except (IndexError, KeyError, TypeError):
        return fallback
    return fallback if value is None else value


# Said on the button rather than in a banner over the mail: whether a body is
# foreign is a judgement this app makes from its letters, and a sentence claiming it
# would be wrong about a Korean mail with a long English thread quoted under it.
TRANSLATE_TIP = ('본문을 한국어로 옮깁니다. 분석과 같은 Codex 한 자리를 쓰므로 '
                 '분석 중이면 그 뒤에 처리됩니다.')


def model_option(row):
    """'GPT-5.6-Sol · 성능 보통 · 사용량 보통 · 추천' — one line in the open list.

    The mark rides in the text because a q-select option is a string; the closed field
    shows the same line, which is what a reader wants to see after choosing it.
    """
    label = model_label(row)
    return f'{label} · 추천' if row.get('recommended') else label


def clipped_note(total, limit=BODY_LIMIT):
    """'본문 123,456자 중 앞부분 20,000자만 분석했습니다' — or '' for an ordinary mail.

    Said out loud and in the warning tone, because a clipped analysis that looked like
    every other analysis is the one way this limit can quietly cost a deadline.
    """
    if not total:
        return ''
    return (f'본문이 {int(total):,}자여서 앞부분 {limit:,}자만 분석했습니다. '
            '뒤쪽에 있는 일정이나 요청은 빠졌을 수 있으니 원문을 확인하세요. '
            '원문은 전체가 보관되어 있습니다.')


def translated_note(language):
    """번역문 위에 붙는 한 줄. What it was, and what it cannot be trusted for."""
    source = f'{language} 원문을' if language else '원문을'
    return f'{source} Codex가 옮긴 것입니다. 금액·수량·날짜·품번은 원문에서 확인하세요.'


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
        # 해외영업 메일: what was stored the one time it was asked for, and whether the
        # mail reads as somebody else's language in the first place. The second is what
        # decides which of the two the panel opens on — not the button being there.
        # 본문이 길어 앞부분만 분석된 메일. The number is the *original* length, which
        # is what the notice has to say — 60,000 is the part that was read.
        'clipped': int(parsed.get('clipped') or 0),
        'translated': row_value(row, 'translated'),
        'language': row_value(row, 'translated_from'),
        'foreign': looks_foreign(tidy_body(parsed.get('body', ''))),
        'draft': row['draft_edit'] or result.get('reply_draft', ''),
        'reply_subject': result.get('reply_subject', '') or f"Re: {row['subject']}",
        'analysed': bool(result),
        'reply_needed': bool(result.get('reply_needed')),
        'todo_hidden': bool(row['todo_hidden']),
    }


# 한 번에 그리는 대화의 줄 수. 대화는 길어져도 읽는 사람이 쫓는 것은 앞의 몇 통과
# 지금 보는 것뿐이고, 스무 줄짜리 띠는 그 아래 분석 결과를 화면 밖으로 밀어낸다.
THREAD_SHOWN = 8


def thread_view(rows, current):
    """이 대화의 메일, 오래된 것부터. 한 통뿐인 대화는 대화가 아니다.

    Returns [] for a lone mail rather than a strip saying '1통 중 1번째', which is the
    dialog telling the reader something they can already see. Everything else here is
    the row the thread query already fetched — no second read per line.
    """
    if len(rows) < 2:
        return []
    shown = []
    for index, row in enumerate(rows, 1):
        shown.append({'id': row['id'], 'nth': index,
                      'subject': row['subject'] or '(제목 없음)',
                      'sender': row['sender'] or '',
                      'day': local_text(row['received'], '%m-%d'),
                      'state': state_of(row),
                      'current': row['id'] == current})
    return shown


def thread_line(rows):
    """'전체 4통 · 지금 보는 것은 3번째'. 세는 것과 위치가 이 띠의 전부다."""
    if not rows:
        return ''
    here = next((row['nth'] for row in rows if row['current']), 0)
    text = f'전체 {len(rows)}통'
    return f'{text} · 지금 보는 것은 {here}번째' if here else text


def thread_clip(rows, shown=THREAD_SHOWN):
    """The lines to draw and how many were left out — the last ones, and the open mail.

    A long thread is clipped from the *front*: the newest turns are what a reader
    came for, and the mail they opened has to be on screen whatever its position,
    or the strip says '3번째' about a row that is not there.
    """
    if len(rows) <= shown:
        return rows, 0
    tail = rows[-shown:]
    if not any(row['current'] for row in tail):
        here = next(row for row in rows if row['current'])
        tail = [here] + tail[1:]
    return tail, len(rows) - len(tail)


def thread_strip(rows, token):
    """이 대화 — 지금 보는 메일이 어디쯤인지, 그리고 나머지로 가는 길.

    Above 분석 결과 rather than beside it: a mail's own analysis reads differently once
    you know it is the fourth turn of a negotiation, so the context comes first.
    """
    from nicegui import ui
    if not rows:
        return
    shown, hidden = thread_clip(rows)
    with ui.element('div').classes('ma-sunken').style('margin-top:12px;padding:10px 12px'):
        with ui.element('div').classes('ma-meta').style('margin-bottom:6px'):
            ui.icon('forum').style(f'color:{MUTED};font-size:15px')
            ui.label('이 대화').classes('ma-head__title').style('font-size:12.5px')
            ui.label(thread_line(rows)).classes('ma-meta__item')
        if hidden:
            ui.label(f'앞선 {hidden}통은 접었습니다').classes('ma-meta__item')
        with ui.element('div').classes('ma-today'):
            for row in shown:
                with ui.element('div').classes('ma-today__row'):
                    ui.label(row['day']).classes('ma-due__day')
                    if row['current']:
                        # 지금 열려 있는 메일. 링크로 두면 자기 자신을 다시 여는
                        # 버튼이 되고, 눌러도 아무 일이 없는 것처럼 보인다.
                        ui.label(row['subject']).classes('ma-today__title') \
                            .style(f'color:{INK};font-weight:650')
                    else:
                        ui.link(row['subject'], href('/mail', token, id=row['id'])) \
                            .classes('ma-today__title')
                    ui.space()
                    ui.label(row['state']).classes('ma-today__kind')


def vendor_path():
    """Where the bundled FullCalendar lives, frozen or not."""
    return Path(__file__).resolve().parent / 'vendor'


def event_title(entry):
    """The entry's own title: no marker, and no clock once the event carries its time.

    The label is built for an Excel cell, which has neither an icon nor a slot to sit
    in, so it spells both out. A timed event prints its own '14:00' beside the icon,
    and the label's copy of it in front of the title made that the second one.
    """
    text = plain_title(entry.label)
    clock = getattr(entry, 'clock', '')
    if clock and text.startswith(clock):
        text = text[len(clock):].lstrip()
    return text


def calendar_events(events, today, handled=()):
    """calendar_sheet entries as FullCalendar events — all of them.

    A mailbox produces hundreds, not thousands, so handing the client the whole set
    lets it page through months without another round trip.

    An entry that knows its time is a *timed* event, not an all-day one: every event
    used to be allDay, so 주 view stacked a 14:00 웨비나 in the 종일 band and left the
    2pm slot it belongs in empty — the one thing a week view is for.
    """
    payload = []
    for day in sorted(events):
        for entry in events[day]:
            if entry.mail_id in handled:
                continue
            clock = getattr(entry, 'clock', '')
            payload.append({
                'id': entry.mail_id,
                'title': event_title(entry),
                'start': f'{day.isoformat()}T{clock}:00' if clock else day.isoformat(),
                'allDay': not clock,
                'color': css_color(COLORS[entry.kind]),
                # `clean` is what the tooltip shows: the title with its time back in
                # front, because a tooltip has no slot to say when from.
                'extendedProps': {'kind': entry.kind, 'evidence': entry.evidence,
                                  'clean': plain_title(entry.label),
                                  # A manual entry owns no mail, so the click that
                                  # opens one has nothing to open and says so instead.
                                  'manual': is_event_key(entry.mail_id),
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


def screen_size():
    """The primary desktop in pixels, or None where there is nothing to ask."""
    try:
        import ctypes
        user32 = ctypes.windll.user32          # Windows only; anything else raises
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return None


def window_size(screen=None, margin=WINDOW_MARGIN):
    """The frame's opening size, shrunk to whatever screen this PC actually has.

    pywebview opens where it is told and a title bar below the desktop cannot be
    dragged back, so a 1366x768 laptop must never be handed the full WINDOW.
    """
    size = []
    for wanted, floor, room in zip(WINDOW, WINDOW_FLOOR, tuple(screen or ()) + (0, 0)):
        room = int(room or 0)
        size.append(wanted if room <= 0 else max(min(wanted, room - margin), floor))
    return tuple(size)


# ---------------------------------------------------------------- page

def section(title, *, top=True):
    """A heading inside a card. Cards carry the frame, so this is only type."""
    from nicegui import ui
    ui.label(title).classes('ma-head__title') \
        .style('margin:%s 0 8px' % ('16px' if top else '0'))


def body_panel(text):
    """원문, with the mail's tables drawn as tables again.

    Built out of elements rather than a string of HTML: the cells go in through
    ui.label, so nothing here can carry markup the sender wrote — the panel needs no
    sanitiser because it never holds the mail's own HTML in the first place.
    """
    from nicegui import ui
    blocks = body_blocks(text)
    if not blocks:
        ui.label('(본문 없음)').style(f'display:block;color:{SUBTLE};font-size:12.5px')
        return
    for kind, content in blocks:
        if kind == 'table':
            head, rows = content[0], content[1:]
            # ma-scroll for its thumb: a table wider than the panel has to say so,
            # and the mail decides how many columns it has.
            with ui.element('div').classes('ma-sheet__wrap ma-scroll'):
                with ui.element('table').classes('ma-sheet'):
                    with ui.element('thead'), ui.element('tr'):
                        for cell in head:
                            with ui.element('th'):
                                ui.label(cell)
                    with ui.element('tbody'):
                        for row in rows:
                            with ui.element('tr'):
                                for cell in row:
                                    with ui.element('td'):
                                        ui.label(cell or '')
        else:
            ui.label(content).style(f'display:block;color:{SUBTLE};font-size:12.5px;'
                                    'white-space:pre-wrap;text-align:left;line-height:1.75;'
                                    'overflow-wrap:anywhere')


def card(title=None, icon=None, flush=False, note=''):
    """The one surface everything sits on. Returns the element, so callers can `with` it.

    `note` is the quiet half of the head: what this card does that looking at it does
    not say — a canvas cannot show a cursor the way a link can.
    """
    from nicegui import ui
    box = ui.element('div').classes('ma-card' + (' ma-card--flush' if flush else ''))
    if title:
        with box, ui.element('div').classes('ma-head'):
            if icon:
                ui.icon(icon).style(f'color:{MUTED};font-size:17px')
            ui.label(title).classes('ma-head__title')
            if note:
                ui.space()
                ui.label(note).classes('ma-meta__item')
    return box


def grid(minimum=300, gap=14):
    """auto-fit columns that collapse to one on a narrow window, which a laptop is."""
    from nicegui import ui
    return ui.element('div').classes('ma-grid') \
        .style(f'gap:{gap}px;grid-template-columns:repeat(auto-fit,minmax({minimum}px,1fr))')


def tag(text, tone=None, soft=None):
    """A coloured label chip. Tone is text, soft is the wash behind it."""
    from nicegui import ui
    if not text:
        return
    ui.label(text).classes('ma-tag').style(f"color:{tone or SUBTLE};"
                                           f"background:{soft or SUNKEN}")


def empty(text):
    from nicegui import ui
    ui.label(text).classes('ma-empty')


def hint(tip, why=''):
    """A mail's hover card, inside whatever element it is called in.

    Built from mail_tip(), which is also what the 메일 목록's own button draws in the
    browser — one shape, so a mail hovered on the 브리핑 and the same mail hovered in
    the list say the same thing. Labels rather than markup: every line here came out
    of a mail, and the only place mail text is ever rendered is rich_text().
    """
    from nicegui import ui
    with ui.tooltip().classes('ma-hint'):
        ui.label(tip['subject']).classes('ma-hint__title')
        ui.label(' · '.join(part for part in (tip['sender'], tip['received']) if part)) \
            .classes('ma-hint__row')
        marks = ' · '.join(part for part in
                           (tip['category'], tip['priority'], tip['state']) if part)
        if marks:
            ui.label(marks).classes('ma-hint__row')
        if tip['summary']:
            ui.label(tip['summary']).classes('ma-hint__row')
        if tip['action']:
            ui.label('다음 행동: ' + tip['action']).classes('ma-hint__row')
        if tip['error']:
            ui.label(tip['error']).classes('ma-hint__row ma-hint__bad')
        if why:
            ui.label('브리핑이 고른 이유: ' + why).classes('ma-hint__why')


def soft_of(color):
    """The 10% wash a tag sits on, from the tag's own colour."""
    return f'{color}1a'


def tag_style(color):
    """Text in the tone, the same tone at 10% behind it. One rule, so tags match."""
    return f'color:{color};background:{color}1a'


def tag_cell(tones, default=SUBTLE, failure=None):
    """A q-td slot that tags a cell by its own text.

    Single quotes throughout: the whole expression lives inside a double-quoted Vue
    attribute, and json.dumps would close it on the first key.
    """
    table = '{' + ','.join(f"'{name}':'{tag_style(color)}'"
                           for name, color in tones.items()) + '}'
    pick = f"{table}[props.value] || '{tag_style(default)}'"
    if failure:
        # '3회 실패' carries its count, so it cannot be a key in the table above.
        pick = f"props.value.includes('실패') ? '{tag_style(failure)}' : ({pick})"
    return ('<q-td :props="props">'
            f'<span v-if="props.value" class="ma-tag" :style="{pick}">'
            '{{ props.value }}</span></q-td>')


TIP_COLUMN = 'tip'


def tip_cell():
    """A q-td slot holding the mail's own hover card, drawn in the browser.

    The row already carries mail_tip(), so a hover costs nothing: asking the server
    for it would be a query per pointer, for text the list fetched with the row. Every
    line is a Vue interpolation rather than markup — what a sender wrote is text here,
    exactly as it is everywhere but rich_text(). Single quotes inside the attributes,
    for the reason tag_cell() has them.
    """
    lines = [
        '<div class="ma-hint__title">{{ props.row.tip.subject }}</div>',
        '<div class="ma-hint__row">{{ [props.row.tip.sender, props.row.tip.received]'
        ".filter(Boolean).join(' · ') }}</div>",
        '<div class="ma-hint__row">{{ [props.row.tip.category, props.row.tip.priority,'
        " props.row.tip.state].filter(Boolean).join(' · ') }}</div>",
        '<div class="ma-hint__row" v-if="props.row.tip.summary">'
        '{{ props.row.tip.summary }}</div>',
        '<div class="ma-hint__row" v-if="props.row.tip.action">'
        '다음 행동: {{ props.row.tip.action }}</div>',
        '<div class="ma-hint__row ma-hint__bad" v-if="props.row.tip.error">'
        '{{ props.row.tip.error }}</div>',
    ]
    return ('<q-td :props="props" style="width:46px;text-align:center">'
            '<q-btn flat dense round size="sm" icon="info_outline" @click.stop '
            f'style="color:{MUTED}">'
            '<q-tooltip class="ma-hint" anchor="bottom right" self="top right" '
            ':offset="[0,6]">' + ''.join(lines) + '</q-tooltip></q-btn></q-td>')


def header_cell(key, sort, desc):
    """A q-th slot that sorts the list by its own column.

    The list is paged in sqlite, so q-table's built-in `sortable` would only reorder
    the fifty rows it happens to be holding. The click comes back here instead, by the
    one route a slot template has: $parent.$emit, which Element.on() is listening for.
    Single quotes inside, for the reason tag_cell() gives.
    """
    live = key == sort
    icon = ('arrow_downward' if desc else 'arrow_upward') if live else 'unfold_more'
    # headerStyle carries the column widths, and a custom header cell is the one place
    # q-table stops applying them for you: without this 수신 loses its fixed width and
    # the date wraps onto two lines.
    return ('<q-th :props="props" :style="props.col.headerStyle" class="ma-th%s" '
            "@click=\"() => $parent.$emit('sortby', '%s')\">"
            '{{ props.col.label }}'
            '<q-icon name="%s" class="ma-th__arrow"></q-icon></q-th>'
            % (' is-live' if live else '', key, icon))


def chart(option, height=200, cap=None):
    """Every chart is an echart; the bundle carries its JavaScript on purpose."""
    from nicegui import ui
    return ui.echart(option).style(f'height:{height}px;width:100%'
                                   + (f';max-width:{cap}px' if cap else ''))


# Said in the card's head rather than left to the pointer: the bars are painted into
# a canvas, which cannot carry a link's colour or a cursor the reader would notice.
BAR_NOTE = '누르면 메일 목록으로'


def bar_link(element, key, names, token):
    """A count bar is the way into the mail behind it.

    The 대시보드 cards have been links since D2 — a number you cannot act on is only
    decoration — and these are the same numbers; they are wired to an event instead of
    drawn as an <a> only because ECharts paints them into a canvas. The filter it opens
    reads `category`/`priority`, the columns the counts themselves are drawn from, so
    the bar and the list it opens can never be counting different mail.
    """
    from nicegui import ui

    def open_list(event):
        name = clicked_bar(event.args, names)
        if name:
            ui.navigate.to(href('/mail', token, **{key: name}))

    return element.on('componentClick', open_list, list(BAR_EVENT))


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
    if name == WAITING:
        # The card and this list count the same mail: core.state_where() filters on the
        # reply_needed column and the same WAIT_DAYS cutoff waiting_replies() used.
        return href('/mail', token, state=WAITING, sort='received', desc='0')
    if name == FAILED_CARD:
        return href('/mail', token, state=FAILED)
    return None


def card_hint(name, data):
    """The line under a card's number. A total says how many, never how long."""
    if name == '미처리 메일' and data.get('oldest'):
        return f"가장 오래된 건 {data['oldest']}일 경과"
    if name == WAITING and data.get('reply_wait'):
        # The list is already sorted by elapsed days, so the first row is the worst one.
        return f"가장 오래 기다린 건 {data['reply_wait'][0]['days']}일째"
    if name == FAILED_CARD:
        # worker.py backs off 5·10·20·40 minutes and then stays hourly, for ever: there
        # is no attempt cap, so the honest line is how often, not whether.
        return '최대 1시간 간격으로 자동 재시도'
    return ''


def card_rows(data):
    """The four fixed cards, plus 답장 대기 and 분석 실패 while there is one to report.

    Both are absent at zero rather than drawn as 0, and 답장 대기 is the one that had
    to earn it: a card counting every mail that ever needed a reply would never fall,
    and core.WAIT_DAYS is the threshold that lets it. 분석 실패 stays last — it is the
    app reporting on itself, and the four above it are the reader's own work.
    """
    rows = list(data['cards'].items())
    if data.get('reply_wait'):
        rows.append((WAITING, len(data['reply_wait'])))
    if data.get('failed'):
        rows.append((FAILED_CARD, data['failed']))
    return rows


def card_icon(name):
    """'…일 내 마감' carries its window in the name, so it cannot be a plain key."""
    return CARD_ICONS.get(name, 'schedule' if name.endswith('일 내 마감') else 'circle')


def cards(data, token):
    from nicegui import ui
    with grid(minimum=190, gap=12):
        for name, value in card_rows(data):
            tone = CARD_TONES.get(name) if value else None
            target = card_target(name, token, data.get('within', DUE_DAYS))
            box = ui.link(target=target) if target else ui.element('div')
            box.classes('ma-kpi')
            with box:
                with ui.element('div').classes('ma-kpi__icon') \
                        .style(f'background:{tone}1a;color:{tone}' if tone else ''):
                    ui.icon(card_icon(name))
                with ui.element('div').style('min-width:0'):
                    ui.label(name).classes('ma-kpi__label')
                    ui.label(str(value)).classes('ma-kpi__value')
                    hint = card_hint(name, data)
                    if hint:
                        ui.label(hint).classes('ma-kpi__hint')


def deadlines(data, today, token, on_tick):
    """The window's deadlines as a checklist, with the bar the ticks move.

    A table would have read the same numbers, but there was nothing to do from it:
    the tick is the whole point, and it writes the mail's own 처리 상태 rather than a
    second list that could disagree with the kanban.
    """
    from nicegui import ui
    rows = deadline_rows(data, today)
    if not rows:
        empty('마감이 있는 일정이 없습니다.')
        return
    done = deadline_progress(rows)
    with ui.element('div').classes('ma-due__head'):
        ui.label(f"완료 {done['done']}").classes('ma-meta__item')
        ui.label(f"남음 {done['left']}").classes('ma-meta__item')
        ui.space()
        ui.label(f"{done['percent']}%").classes('ma-meta__item')
    ui.linear_progress(done['ratio'], show_value=False).classes('ma-progress') \
        .props('size=6px rounded')
    with ui.element('div').classes('ma-duelist'):
        for row in rows:
            with ui.element('div').classes('ma-due' + (' is-done' if row['done'] else '')):
                ui.checkbox(value=row['done'],
                            on_change=lambda event, ident=row['mail']:
                            on_tick(ident, event.value)) \
                    .props('dense size=xs').tooltip('체크하면 처리 완료가 됩니다')
                ui.icon(KIND_ICONS[row['kind']]) \
                    .style(f'color:{css_color(COLORS[row["kind"]])};font-size:14px;flex:none') \
                    .tooltip(row['kind'])
                ui.label(row['day']).classes('ma-due__day')
                if row['manual']:
                    ui.label(row['title']).classes('ma-due__title')
                else:
                    ui.link(row['title'], href('/mail', token, id=row['mail'])) \
                        .classes('ma-due__title')
                ui.space()
                ui.label(row['left']).classes(
                    'ma-due__left' + (' is-missed' if row['missed'] else ''))


# 이 앱은 보낸 메일을 볼 수 없다 — POP3는 받은 것만 말하고, mailto로 보낸 답장은
# 이 프로세스를 거치지 않는다. 그래서 이 패널이 아는 것은 '완료 표시가 없다'까지이고,
# 밑에 한 줄로 그렇게 말한다. 말하지 않으면 '당신은 답장을 안 했습니다'로 읽히는데,
# 웹메일에서 답장하고 표시만 안 한 사람에게 그것은 틀린 문장이다.
WAIT_NOTE = '보낸 메일은 볼 수 없어 완료 표시를 기준으로 셉니다'
# 한 주를 넘긴 것만 색을 얻는다. WAIT_DAYS를 막 넘긴 이틀째까지 붉으면 이 패널은
# 전부 붉고, 전부 붉은 목록은 어느 줄이 나쁜지 말하지 않는다.
WAIT_LATE = 7


def wait_age(days):
    """'2일째'. 경과일이 이 줄의 전부라 제목보다 먼저 읽히는 자리에 놓는다."""
    return f'{days}일째'


def waiting_panel(rows, token, on_done):
    """답장 대기 — 오래 기다린 것부터, 그 자리에서 완료할 수 있게.

    The 완료 checkbox is why this is a panel and not a second count: a list that only
    says '당신은 늦었다' and makes you go somewhere else to answer it is a list a reader
    learns to skip. It writes the mail's own `handled`, the same field the kanban and
    the 마감 checklist move, so no two screens can disagree about what is finished.
    """
    from nicegui import ui
    with card(flush=True):
        with ui.element('div').classes('ma-lane__head'):
            ui.icon('reply').style(f'color:{MUTED};font-size:17px')
            ui.label(WAITING).classes('ma-head__title')
            if rows:
                ui.label(f'{len(rows)}건').classes('ma-meta__item')
            ui.space()
            ui.label(WAIT_NOTE).classes('ma-meta__item')
        if not rows:
            empty(f'{WAIT_DAYS}일 넘게 답장을 기다리는 메일이 없습니다.')
            return
        with ui.element('div').classes('ma-duelist'):
            for row in rows:
                with ui.element('div').classes('ma-due'):
                    ui.checkbox(value=False,
                                on_change=lambda event, ident=row['id']:
                                on_done(ident, event.value)) \
                        .props('dense size=xs').tooltip('체크하면 처리 완료가 됩니다')
                    ui.label(local_text(row['received'], '%m-%d')).classes('ma-due__day')
                    ui.link(row['subject'], href('/mail', token, id=row['id'])) \
                        .classes('ma-due__title')
                    if row['progress']:
                        # 진행 중은 잊은 것이 아니다. 줄을 빼지는 않는다 — 열흘째 진행
                        # 중인 메일이야말로 이 패널이 있는 이유다 — 대신 그렇게 적는다.
                        tag(PROGRESS, tone=css_color(LINK), soft=SUNKEN)
                    ui.space()
                    ui.label(wait_age(row['days'])).classes(
                        'ma-due__left' + (' is-missed' if row['days'] >= WAIT_LATE else ''))


def briefing_frame():
    """The beamed card itself, built once and never rebuilt.

    The body inside it is refreshable; this is not. A CSS animation restarts from
    zero every time its element is created, so a beam inside the refreshable would
    jump back to the top of its lap on every five-second repaint — the same reason
    the charts beside it are updated in place rather than wrapped in one.
    """
    from nicegui import ui
    return ui.element('div').classes('ma-card ma-beam')


def briefing_body(view, today, token, reason, on_ask=None):
    """오늘의 AI 브리핑, inside briefing_frame(). Reads what the worker stored.

    Generating here would hold the one Codex slot for up to 240 seconds inside a page
    that repaints every five, and the reader would watch the 대시보드 stop. 다시 만들기
    books it with the worker and says so, exactly as 다시 분석 does.
    """
    from nicegui import ui
    with ui.element('div').classes('ma-head'):
        ui.icon('auto_awesome').style(f'color:{BRAND};font-size:17px')
        ui.label('오늘의 AI 브리핑').classes('ma-head__title')
        if view['has']:
            ui.label(view['stamp']).classes('ma-meta__item')
        ui.space()
        ui.button('다시 만들기', icon='refresh', on_click=on_ask) \
            .props('flat dense no-caps text-color=primary').tooltip(BRIEF_TIP)
    if view['has'] and view['stale']:
        with ui.element('div').classes('ma-alert ma-alert--warn').style('margin-bottom:11px'):
            ui.icon('history').style('font-size:15px')
            ui.label(stale_text(view, today))
    if not view['has']:
        empty(reason)
        return
    if view['headline']:
        ui.label(view['headline']).classes('ma-brief__lede')
    with ui.element('div').classes('ma-brief__grid'):
        for part in view['sections']:
            tone = BRIEF_TONES[part['accent']]
            with ui.element('div').classes('ma-brief__sec') \
                    .style(f"--sec:{tone}"):
                with ui.element('div').classes('ma-brief__head'):
                    ui.icon(part['icon']).style(f'color:{tone};font-size:14px')
                    ui.label(part['title']).classes('ma-brief__title')
                for line in part['lines']:
                    with ui.element('div').classes('ma-brief__line'):
                        ui.label(line)
    if view['watch']:
        with ui.element('div').classes('ma-brief__watch'):
            ui.label('먼저 볼 메일').classes('ma-meta__item')
            for pin in view['watch']:
                with ui.link(target=href('/mail', token, id=pin['mail_id'])) \
                        .classes('ma-brief__pin'):
                    ui.icon('arrow_outward')
                    # The mail's own subject, and the sentence Codex wrote about it in
                    # the hover: four chips all reading '…를 확인하세요' said which mail
                    # only to whoever already knew.
                    ui.label(pin['title'])
                    hint(pin['tip'], pin['reason'])


def today_panel(rows, today, token):
    """오늘 일정, on the 대시보드, from the same events the 일정 화면 draws.

    The calendar is a page you go to; this is the one line of it you need without
    going anywhere — which is the whole reason 현황 became 대시보드.
    """
    from nicegui import ui
    with card(flush=True):
        with ui.element('div').classes('ma-lane__head'):
            ui.icon('today').style(f'color:{MUTED};font-size:17px')
            ui.label('오늘 일정').classes('ma-head__title')
            ui.label(day_title(today)).classes('ma-meta__item')
            ui.space()
            ui.link('달력 열기', href('/calendar', token)) \
                .style(f'color:{BRAND};font-size:12px;text-decoration:none')
        if not rows:
            empty('오늘 예정된 일정이 없습니다.')
            return
        with ui.element('div').classes('ma-today'):
            for row in rows:
                with ui.element('div').classes('ma-today__row'):
                    ui.icon(KIND_ICONS[row['kind']]) \
                        .style(f'color:{css_color(COLORS[row["kind"]])};font-size:15px;'
                               'flex:none')
                    if row['manual']:
                        # No mail behind it, so no link: a href that opens an empty
                        # 메일 화면 is worse than plain text.
                        ui.label(row['title']).classes('ma-today__title') \
                            .style(f'color:{INK}')
                    else:
                        ui.link(row['title'], href('/mail', token, id=row['mail'])) \
                            .classes('ma-today__title')
                    ui.space()
                    ui.label(row['kind']).classes('ma-today__kind')


EVENT_HINT = '2026-09-22 또는 2026-09-22 15:00'


def event_text(value):
    """A stored date as the panel prints it, or '—'. Never a strftime: see local_text."""
    day, clock = parse_day(value)
    if not day:
        return '—'
    return f'{day.isoformat()} {clock}'.strip()


def event_error(title, start, deadline):
    """Why this 일정 cannot be saved, or '' when it can.

    An unparsable date is the failure worth catching: parse_day() simply returns None
    for one, and collect() then builds no entry at all — the event would be saved,
    show up in the list below, and appear on no calendar, with nothing saying why.
    """
    if not str(title or '').strip():
        return '일정 제목을 입력하세요.'
    for label, value in (('시작', start), ('마감', deadline)):
        if str(value or '').strip() and not parse_day(value)[0]:
            return f'{label} 날짜를 읽을 수 없습니다. {EVENT_HINT} 형식으로 입력하세요.'
    if not str(start or '').strip() and not str(deadline or '').strip():
        return '시작이나 마감 중 하나는 있어야 달력에 올라갑니다.'
    return ''


def manual_rows(events):
    """직접 추가한 일정 as the panel under the calendar lists them, newest first."""
    return [{'id': row['id'], 'title': row['title'],
             'start': event_text(row['start']), 'deadline': event_text(row['deadline']),
             'note': row['note'], 'done': row['handled'] == HANDLED}
            for row in reversed(list(events))]


def manual_panel(directory, account, token, on_change):
    """Add a 일정 by hand, and list the ones that are there.

    Everything else on this page comes out of an analysis, and a mailbox that never
    mentions a date cannot be made to mention one. The calendar above is drawn from a
    payload baked into the page, so a change here reloads rather than repaints: there
    is no server-side handle on a FullCalendar that was built in the browser.
    """
    from nicegui import ui
    rows = manual_rows(store(directory).events(account)) if account else []

    def add():
        problem = event_error(title.value, start.value, deadline.value)
        if problem:
            ui.notify(problem)
            return
        if not account:
            ui.notify('설정을 먼저 저장하세요.')
            return
        store(directory).add_event(account, (title.value or '').strip(),
                                   (start.value or '').strip(),
                                   (deadline.value or '').strip(),
                                   (note.value or '').strip())
        on_change()

    def drop(ident):
        store(directory).delete_event(ident)
        on_change()

    with card('직접 추가한 일정', 'edit_calendar'):
        ui.label(f'분석이 잡지 못한 일정을 손으로 올립니다. 날짜는 {EVENT_HINT} 형식이고, '
                 '시작과 마감 중 하나만 있어도 됩니다. 메일에 딸리지 않으므로 누를 메일이 '
                 '없고, Excel 일정 시트에는 나가지 않습니다.').classes('ma-lede')
        with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap;'
                                     'align-items:center;margin-bottom:12px'):
            title = ui.input(placeholder='일정 제목').props('dense outlined') \
                .style('flex:2 1 220px')
            start = ui.input(placeholder='시작 2026-09-22 15:00').props('dense outlined') \
                .style('flex:1 1 180px')
            deadline = ui.input(placeholder='마감 2026-09-22').props('dense outlined') \
                .style('flex:1 1 180px')
            note = ui.input(placeholder='메모 (선택)').props('dense outlined') \
                .style('flex:1 1 160px')
            ui.button('추가', icon='add', on_click=add).props('unelevated dense no-caps')
        if not rows:
            empty('직접 추가한 일정이 없습니다.')
            return
        for row in rows:
            with ui.element('div').classes('ma-row'):
                ui.label(row['title']) \
                    .style(f"color:{MUTED if row['done'] else INK};font-size:13px;"
                           'font-weight:500'
                           + (';text-decoration:line-through' if row['done'] else ''))
                ui.label(f"시작 {row['start']} · 마감 {row['deadline']}") \
                    .classes('ma-meta__item')
                if row['note']:
                    ui.label(row['note']).classes('ma-meta__item')
                if row['done']:
                    tag('완료', MUTED, SUNKEN)
                ui.space()
                ui.button(icon='delete_outline', on_click=lambda i=row['id']: drop(i)) \
                    .props('flat dense round text-color=negative').tooltip('삭제')


def todo_tally(counts, token):
    """할 일 in three numbers on 대시보드, because the board is a page nobody passes by."""
    from nicegui import ui
    with card(flush=True):
        with ui.element('div').classes('ma-lane__head'):
            ui.icon('checklist').style(f'color:{MUTED};font-size:17px')
            ui.label('할 일').classes('ma-head__title')
            ui.space()
            ui.link('할 일 판 열기', href('/todo', token)) \
                .style(f'color:{BRAND};font-size:12px;text-decoration:none')
        if not counts['total']:
            empty('메일에서 나온 할 일도, 직접 적은 것도 없습니다.')
            return
        with ui.element('div').classes('ma-tally'):
            for state, label in COLUMNS:
                with ui.element('div').classes('ma-tally__cell'):
                    with ui.element('div').classes('ma-tally__head'):
                        ui.element('div').classes('ma-dot') \
                            .style(f'background:{LANE_TONES[state]}')
                        ui.label(label).classes('ma-meta__item')
                    ui.label(str(counts[state])).classes('ma-tally__n')
        ui.linear_progress(counts['ratio'], show_value=False).classes('ma-progress') \
            .props('size=6px rounded')
        ui.label(f"{counts['total']}건 중 {counts[HANDLED]}건 완료") \
            .classes('ma-meta__item').style('padding:2px 18px 14px')


def memo_paper(view):
    """The coloured card a memo is written on. Returned so the caller can fill it.

    The ground and the left edge are the memo's whole structure, so they are set from
    the view rather than a class: six papers would otherwise be six more rules in
    THEME saying nothing but a colour each.
    """
    from nicegui import ui
    return ui.element('div').classes('ma-memo').style(
        f"background:{view['ground']};border-color:{view['edge']}55;"
        f"border-left-color:{view['edge']}")


def memo_mail_link(view, token):
    """'this memo belongs to that mail' — the one thing a 할 일 card cannot say."""
    from nicegui import ui
    if not view['mail']:
        return
    with ui.link(target=href('/mail', token, id=view['mail'])).classes('ma-memo__link'):
        ui.icon('mail')
        ui.label(view['subject'] or '(제목 없음)')


def pinned_panel(views, token, total=0):
    """고정한 메모 on 대시보드. Read-only: a memo is written on the wall, not here.

    No textarea, on purpose — the 대시보드 repaints itself every REFRESH_SECONDS, and
    an editable box on a block that rebuilds on a timer is precisely the edit-eating
    the 메모 화면 goes out of its way to avoid.
    """
    from nicegui import ui
    with card(flush=True):
        with ui.element('div').classes('ma-lane__head'):
            ui.icon('push_pin').style(f'color:{MUTED};font-size:17px')
            ui.label('고정한 메모').classes('ma-head__title')
            if total > len(views):
                ui.label(f'{len(views)}/{total}').classes('ma-meta__item')
            ui.space()
            ui.link('메모 열기', href('/memo', token)) \
                .style(f'color:{BRAND};font-size:12px;text-decoration:none')
        if not views:
            empty('고정한 메모가 없습니다. 메모 화면에서 압정을 누르면 여기에 올라옵니다.')
            return
        with ui.element('div').style('display:grid;gap:8px;padding:2px 18px 16px'):
            for view in views:
                with memo_paper(view):
                    ui.label(view['text']).classes('ma-memo__body')
                    memo_mail_link(view, token)
                    with ui.element('div').classes('ma-memo__foot'):
                        ui.label(view['when']).classes('ma-memo__when')


def lamp(running, stopping=False):
    """(colour, label) for the worker's state, shared by the strip and the 실행 page."""
    if stopping:
        return css_color(SOON), '중지 중'
    return (OK, '실행 중') if running else (MUTED, '중지됨')


def run_strip(summary, token=None, state=None):
    """수집 as the 대시보드 shows it: one line, and the log behind a fold.

    The log is shut unless somebody opened it — it is the longest block in that column
    and the least often read, and the question this card is on the 대시보드 to answer
    is 실행 중 or 중지됨, which is the line that stays. `state` is the reader's own
    choice and belongs to the page, because the card is rebuilt on every beat.
    """
    from nicegui import ui
    state = state if state is not None else {'open': False}
    tone, _ = lamp(summary['running'], summary['stopping'])
    folded = bool(summary['message'] or summary['lines'])
    live = ' is-open' if state['open'] else ''

    def fold():
        """A class swap and nothing else: a refresh here would rebuild the animation."""
        state['open'] = not state['open']
        for element in (shut, mark):
            element.classes(add='is-open') if state['open'] \
                else element.classes(remove='is-open')

    with card():
        with ui.element('div').classes('ma-head').style('margin-bottom:6px'):
            ui.element('div').classes('ma-dot').style(f'background:{tone}')
            ui.label(summary['label']).classes('ma-head__title')
            ui.space()
            if token:
                ui.link('실행 화면 열기', href('/run', token)) \
                    .style(f'color:{BRAND};font-size:12px;text-decoration:none')
            if folded:
                # Nothing to open is nothing to offer, which is the rule an empty badge
                # and the absent 분석 실패 card already follow.
                opener = ui.button(on_click=fold).props('flat dense round size=sm') \
                    .style(f'color:{MUTED}').tooltip('최근 기록 보기·숨기기')
                with opener:
                    mark = ui.icon('expand_more').classes('ma-fold__mark' + live)
        with ui.element('div').classes('ma-meta') \
                .style('margin-bottom:10px' if folded else ''):
            for label, value in summary['stamps'].items():
                ui.label(f'{label} {value}').classes('ma-meta__item')
        if not folded:
            return
        shut = ui.element('div').classes('ma-fold' + live)
        with shut, ui.element('div'):
            if summary['message']:
                ui.label(summary['message']) \
                    .style(f'color:{INK};font-size:12.5px;margin-bottom:8px')
            if summary['lines']:
                with ui.element('div').classes('ma-sunken ma-scroll ma-log') \
                        .style('max-height:180px;width:100%'):
                    for text in summary['lines']:
                        ui.label(text).style('white-space:pre-wrap;overflow-wrap:anywhere')


# Drawn, not fetched: the mark has to be there on a PC with no network and behind a
# proxy, and one <svg> costs less than an asset the spec would have to carry.
BRAND_MARK = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="2.75" y="5.25" width="18.5" height="13.5" rx="2.25"/>'
    '<path d="m3.4 7 8.6 6 8.6-6"/></svg>')


def badge_text(count):
    """The number beside a destination, or '' when there is nothing to say.

    Nothing is drawn for a zero: a badge reading 0 every day is how a reader learns to
    stop looking at that corner, which is the rule the 분석 실패 card already follows.
    """
    try:
        number = int(count or 0)
    except (TypeError, ValueError):
        return ''
    if number <= 0:
        return ''
    return f'{NAV_BADGE_MAX}+' if number > NAV_BADGE_MAX else str(number)


def nav_rows(current, counts=None):
    """The sidebar as it is drawn: [(heading, [(path, name, icon, live, badge)])]."""
    known = {path: (name, icon) for path, name, icon in PAGES}
    counts = counts or {}
    return [(heading, [(path, known[path][0], known[path][1], path == current,
                        badge_text(counts.get(path)))
                       for path in paths])
            for heading, paths in NAV_GROUPS]


def bar_status(state):
    """The header's answer to '지금 수집이 돌고 있나'. `state` is hub.state(), or None.

    It says the same three words 실행 says, because a chip that invented its own
    vocabulary would be a second thing to learn for the same fact.
    """
    if not state:
        return {'text': '', 'tone': MUTED, 'beat': False}
    if state.get('stopping'):
        return {'text': '중지 중…', 'tone': css_color(SOON), 'beat': True}
    if state.get('running'):
        return {'text': '수집 중', 'tone': OK, 'beat': True}
    return {'text': '수집 멈춤', 'tone': MUTED, 'beat': False}


def usage_chip(view):
    """The header's answer to '한도가 얼마나 남았나'. `view` is usage.Meter.view().

    Drawn only once Codex has actually answered: a quota this app has not managed to
    read is not a quota at 0%, which is the rule badge_text() follows for a zero and
    recheck() follows for a check that never reached GitHub.
    """
    view = view or {}
    lines = [line for line in (view.get('lines') or []) if line]
    return {'text': str(view.get('text') or ''),
            'tone': USAGE_TONES.get(view.get('level'), MUTED),
            'tip': '\n'.join(lines),
            'shown': bool(view.get('known') and view.get('text'))}


def update_pill(offer):
    """'새 버전 0.5.1', or ''. The header only ever says there is one; 실행 says what
    it costs and is where the button lives."""
    version = (offer or {}).get('version') if isinstance(offer, dict) else None
    return f'새 버전 {version}' if version else ''


def shell(current, token, chrome=None, watch=None, usage=None):
    """The sidebar, the header band and the page area, identical on every page.

    `chrome` is one callable returning {'counts', 'state', 'offer'} — one call per tick
    for all three, because the badges, the status chip and the update pill move on the
    same beat and re-reading the mailbox once per element is what it is there to avoid.
    It is passed in rather than taken from a hub, so this module keeps importing without
    nicegui and nav_rows()/bar_status() stay coverable on Linux.

    `watch` is the update re-check, on its own much slower beat. It lives here rather
    than on the 대시보드 because this is the one thing every page has, and an app that
    is open all day is exactly the one that never sees a launch-time check again.

    `usage` is the Codex 사용량 read, on a beat of its own for the same reason: the
    answer to '분석이 왜 안 되지' is often 'this account is out of messages until 14:16',
    and that is a thing to be able to see rather than to deduce from a failure line.
    """
    from contextlib import contextmanager
    from nicegui import ui

    titles = {path: name for path, name, _ in PAGES}

    @contextmanager
    def frame():
        # Quasar's own palette otherwise ships a blue that is not this app's blue,
        # and every button, toggle and spinner would wear it.
        ui.colors(primary=BRAND, secondary=SUBTLE, positive=OK, negative=css_color(URGENT),
                  warning=css_color(SOON))
        ui.add_head_html(THEME)
        # Before the app mounts, so a sidebar left collapsed does not open and shut.
        ui.add_head_html(RAIL_BOOT)
        latest = chrome() if chrome is not None else {}
        marks = {}

        def paint(state, line, quota):
            """Everything about the chips and the pill that is not their text. Bound
            late on purpose: the elements below do not exist yet when this is read."""
            dot.style(f"background:{state['tone']}")
            if state['beat']:
                chip.classes(add='is-live')
            else:
                chip.classes(remove='is-live')
            chip.set_visibility(bool(state['text']))
            pill.set_visibility(bool(line))
            spark.style(f"background:{quota['tone']}")
            band.style(f"color:{quota['tone']}" if quota['tone'] != MUTED else '')
            band.set_visibility(quota['shown'])

        with ui.element('div').classes('ma-shell'):
            with ui.element('aside').classes('ma-side'):
                with ui.element('div').classes('ma-side__top'):
                    with ui.link(target=href('/', token)).classes('ma-side__brand'):
                        with ui.element('div').classes('ma-side__mark'):
                            ui.html(BRAND_MARK)
                        with ui.element('div').classes('ma-side__words'):
                            ui.label('메일 업무 도우미').classes('ma-side__name')
                            ui.label(__version__).classes('ma-side__ver')
                    # Nothing here reaches the server: the class it writes is read by
                    # CSS, and the choice is kept in the browser. In the rail it takes
                    # the mark's own square on hover, which is the only press left.
                    fold = ui.button(icon='menu_open').classes('ma-side__fold') \
                        .props('flat dense round size=sm').style(f'color:{SUBTLE}')
                    fold.on('click', js_handler=RAIL_TOGGLE)
                    with fold:
                        # To the right, where the rail's own row tooltips are: below
                        # the button it lands on the first nav row it just hid.
                        ui.tooltip('사이드바 접기·펼치기') \
                            .props('anchor="center right" self="center left"')
                with ui.element('nav'):
                    for heading, items in nav_rows(current, latest.get('counts')):
                        if heading:
                            ui.label(heading).classes('ma-side__group')
                        for path, name, icon, live, badge in items:
                            classes = 'ma-side__item' + (' is-live' if live else '')
                            link = ui.link(target=href(path, token)).classes(classes)
                            # The rail draws this with ::after; the name has to be on
                            # the row itself because the label is display:none there.
                            link.props(f'data-name="{name}"')
                            with link:
                                ui.icon(icon)
                                ui.label(name).classes('ma-side__label')
                                mark = ui.label(badge).classes('ma-badge')
                                mark.set_visibility(bool(badge))
                                marks[path] = mark
            with ui.element('div').classes('ma-main'):
                with ui.element('header').classes('ma-bar'):
                    with ui.element('div').classes('ma-bar__inner'):
                        # The frame has no browser chrome, so this is the only way back
                        # from a page somebody reached by following a card.
                        ui.button(icon='arrow_back', on_click=lambda: ui.navigate.back()) \
                            .props('flat dense round size=sm').style(f'color:{SUBTLE}') \
                            .tooltip('뒤로')
                        ui.label(titles.get(current, '')).classes('ma-bar__title')
                        ui.space()
                        quota = usage_chip(latest.get('usage'))
                        band = ui.element('div').classes('ma-chip')
                        with band:
                            spark = ui.element('div').classes('ma-chip__dot') \
                                .style(f"background:{quota['tone']}")
                            gauge = ui.label(quota['text'])
                            # One tooltip, whose text the tick rewrites: the two windows
                            # and the reset clock are the whole answer, and a chip that
                            # spelled them out would be wider than the title beside it.
                            note = ui.tooltip(quota['tip']).props('anchor="bottom middle" '
                                                                 'self="top middle"') \
                                .style('white-space:pre-line;text-align:left')
                        shown = bar_status(latest.get('state'))
                        chip = ui.element('div').classes('ma-chip')
                        with chip:
                            dot = ui.element('div').classes('ma-chip__dot') \
                                .style(f"background:{shown['tone']}")
                            word = ui.label(shown['text'])
                        line = update_pill(latest.get('offer'))
                        with ui.link(target=href('/run', token)).classes('ma-pill') as pill:
                            ui.icon('system_update_alt')
                            offer = ui.label(line)
                        paint(shown, line, quota)
                with ui.element('main').classes('ma-page') as page:
                    yield page

        def tick():
            """Plain element methods only — no ui.* here. A tick that resolved a client
            would be the 지금 확인 failure again, and nothing on screen would say so."""
            data = chrome() or {}
            counts = data.get('counts') or {}
            for path, label in marks.items():
                text = badge_text(counts.get(path))
                if text != label.text:
                    label.set_text(text)
                    label.set_visibility(bool(text))
            state = bar_status(data.get('state'))
            found = update_pill(data.get('offer'))
            quota = usage_chip(data.get('usage'))
            if (state['text'] != word.text or found != offer.text
                    or quota['text'] != gauge.text):
                word.set_text(state['text'])
                offer.set_text(found)
                gauge.set_text(quota['text'])
                note.set_text(quota['tip'])
                paint(state, found, quota)

        async def look():
            """A version found while somebody is working: the pill and one line.

            Never the dialog the 대시보드 opens. That one lands at launch, on a page
            nobody has typed into yet; this one can arrive over a draft, a memo or a
            half-written 상담 question, and a dialog there is the edit-eating every
            other screen in this app is built to avoid. The pill itself needs no help:
            chrome() reads the same `offer`, so the next tick paints it.
            """
            found = await watch()
            if found:
                ui.notify(f"새 버전 {found.get('version', '')}이(가) 나왔습니다. "
                          '실행 화면에서 설치할 수 있습니다.', type='info', timeout=10000)

        if chrome is not None:
            # Built while the page is, never inside a handler that has refreshed:
            # a timer takes its client from the current slot.
            ui.timer(REFRESH_SECONDS, tick)
        if watch is not None:
            ui.timer(WATCH_SECONDS, look)
        if usage is not None:
            # Once just after the page is drawn, and then on its own beat. Every page
            # load asks, and all but the first are a no-op: Meter.watch() answers from
            # the reading it already has while that reading is fresh.
            ui.timer(USAGE_FIRST, usage, once=True)
            ui.timer(USAGE_SECONDS, usage)

    return frame()


def refused():
    from nicegui import ui
    ui.add_head_html(THEME)
    with ui.element('div').classes('ma-page'):
        with card():
            ui.label('이 주소로는 열 수 없습니다. 메일 도우미가 띄운 창에서 다시 열어 주세요.') \
                .style(f'color:{INK};font-size:13px')


def chips(pairs):
    """Small grey facts in a row: sender, time, category, state."""
    from nicegui import ui
    with ui.element('div').classes('ma-meta').style('margin:4px 0 12px'):
        for text, tone in pairs:
            if text:
                ui.label(text).classes('ma-meta__item').style(f'color:{tone}' if tone else '')


# In the head, without async/defer: a body script loads while run_javascript is
# already firing, and the initialiser then finds window.FullCalendar undefined.
# 할 일: the drag itself is handled in the browser and never reaches the server.
# dragover fires continuously while the pointer travels, and one websocket message per
# frame would cost more than the board rebuild it is decorating; only the drop emits.
DRAG_END = "(e) => e.currentTarget.classList.remove('is-dragging')"
DRAG_OVER = ("(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move';"
             " e.currentTarget.classList.add('is-over'); }")
# dragleave also fires on the way *into* a child, so the lane only stops glowing once
# the pointer has actually left it.
DRAG_LEAVE = ("(e) => { if (!e.currentTarget.contains(e.relatedTarget))"
              " e.currentTarget.classList.remove('is-over'); }")
DRAG_DROP = ("(e) => { e.preventDefault(); e.currentTarget.classList.remove('is-over');"
             " emit(e.dataTransfer.getData('text/plain')); }")


def drag_start(payload):
    """The card's identity rides in the drag, so the drop needs no server state.

    json.dumps and not an f-string: the payload ends in a mail id, which is whatever
    the sender's mail client wrote, quotes included.
    """
    return ("(e) => { e.dataTransfer.effectAllowed = 'move';"
            " e.dataTransfer.setData('text/plain', %s);"
            " e.currentTarget.classList.add('is-dragging'); }" % json.dumps(payload))


# One icon per kind, drawn at one size. The ◾/▫/· of the Excel cell are three
# different glyph sizes, which beside each other read as a font that failed rather
# than as three kinds; Quasar's Material Icons are already on every page.
KIND_ICONS = {'마감': 'flag', '시작': 'play_arrow', '확인 필요': 'help_outline'}

CALENDAR_SCRIPT = '<script src="/vendor/fullcalendar/index.global.min.js"></script>'
CALENDAR_SETUP = """
<script>
// Korean strings are set here rather than by loading a locale bundle: FullCalendar 6
// ships locales as a separate package and this is the whole of what we need.
// One tooltip element for the whole page, not one per event: a month view mounts
// and unmounts hundreds of them as you page through it.
window.mailTip = function () {
  let tip = document.getElementById('ma-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'ma-tip';
    tip.className = 'ma-tip';
    document.body.appendChild(tip);
  }
  return tip;
};
function escapeHtml(text) {
  const box = document.createElement('div');
  box.textContent = String(text == null ? '' : text);
  return box.innerHTML;
}
function hideTip() {
  window.mailTip().classList.remove('is-on');
}
function showTip(anchor, html) {
  const tip = window.mailTip();
  tip.innerHTML = html;
  tip.classList.add('is-on');
  // Measured after the content is in, and clamped to the viewport: an event in the
  // last column would otherwise put half the tooltip off the right edge.
  const box = anchor.getBoundingClientRect();
  const size = tip.getBoundingClientRect();
  const margin = 8;
  let left = box.left + box.width / 2 - size.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - size.width - margin));
  let top = box.top - size.height - 8;
  if (top < margin) top = box.bottom + 8;      // no room above; sit under it instead
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}
// A tooltip is anchored to where the element was when the pointer arrived, so any
// scroll or view change has to take it away rather than leave it pointing at nothing.
window.addEventListener('scroll', hideTip, true);
window.addEventListener('resize', hideTip);

const MA_KIND_ICONS = __KIND_ICONS__;

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
    // A timed event renders as a dot in dayGrid by default, which would turn the
    // month view into three shades of speck; the block is what carries the colour.
    eventDisplay: 'block',
    // 24-hour, two digits: FullCalendar's own default is '2p', and every label this
    // app writes elsewhere is '14:00'.
    eventTimeFormat: {hour: '2-digit', minute: '2-digit', hour12: false},
    slotLabelFormat: {hour: '2-digit', minute: '2-digit', hour12: false},
    scrollTime: '08:00:00',
    nowIndicator: true,
    headerToolbar: {left: 'prev,next today', center: '',
                    right: 'dayGridMonth,timeGridWeek,listMonth'},
    buttonText: {today: '오늘', month: '월', week: '주', list: '목록'},
    allDayText: '종일',
    noEventsText: '이 기간에는 일정이 없습니다.',
    moreLinkText: (n) => `외 ${n}건`,
    dayHeaderContent: (arg) => weekdays[arg.date.getDay()],
    datesSet: (info) => {
      // Paging months rebuilds every event element, and the one the pointer was over
      // goes with them — without this the tooltip outlives its own anchor.
      hideTip();
      // 주 is the one view with a 24-hour column behind it: at height 'auto' it draws
      // all of it, so a 14:00 webinar sits a screen and a half below the fold and
      // midnight is what the page opens on. A fixed height gives the grid its own
      // scroll, which is what makes scrollTime mean anything.
      const tall = info.view.type.startsWith('timeGrid')
        ? Math.max(520, window.innerHeight - 250) : 'auto';
      if (calendar.getOption('height') !== tall) {
        calendar.setOption('height', tall);
        // After the tick, not in it: setOption rebuilds the scroller this is trying
        // to scroll, so the call in here would land on the element being replaced
        // and 주 would open on midnight with the day's events below the fold.
        if (tall !== 'auto') setTimeout(() => calendar.scrollToTime('08:00:00'), 0);
      }
      const label = document.getElementById('calendar-title');
      if (!label) return;
      const start = info.view.currentStart;
      const last = new Date(info.view.currentEnd.getTime() - 86400000);
      label.textContent = info.view.type === 'dayGridMonth'
        ? `${start.getFullYear()}년 ${start.getMonth() + 1}월`
        : `${stamp(start)} ~ ${stamp(last)}`;
    },
    // The kind's icon, the time, then the title — one row, one icon size, in every
    // view. Built as nodes rather than innerHTML: the title is whatever the sender
    // wrote and must never be parsed as markup.
    eventContent: (arg) => {
      const box = document.createElement('div');
      box.className = 'ma-ev';
      const icon = document.createElement('i');
      icon.className = 'material-icons ma-ev__icon';
      icon.textContent = MA_KIND_ICONS[arg.event.extendedProps.kind] || 'circle';
      // 목록 draws the event on the page's own white, where an icon in currentColor
      // would be the only thing on the row not saying which kind it is.
      const list = arg.view.type.startsWith('list');
      if (list) icon.style.color = arg.event.backgroundColor;
      box.appendChild(icon);
      // 목록 has a column of its own for the time; anywhere else this is the only
      // place it can be said.
      if (arg.timeText && !list) {
        const when = document.createElement('span');
        when.className = 'ma-ev__time';
        when.textContent = arg.timeText;
        box.appendChild(when);
      }
      const title = document.createElement('span');
      title.className = 'ma-ev__title';
      title.textContent = arg.event.title;
      box.appendChild(title);
      return {domNodes: [box]};
    },
    eventDidMount: (info) => {
      // No info.el.title: the desktop tooltip waits a second, wraps where it likes and
      // cannot show the kind's colour. tip() is the page's own, in the chart's slate.
      const props = info.event.extendedProps;
      const body = [];
      body.push(`<div class="ma-tip__row"><b>${stamp(info.event.start)}</b>`
                + ` ${weekdays[info.event.start.getDay()]}요일</div>`);
      if (props.missed) body.push('<div class="ma-tip__row ma-tip__miss">마감이 지났습니다</div>');
      if (props.evidence) {
        body.push(`<div class="ma-tip__row">근거: ${escapeHtml(props.evidence)}</div>`);
      }
      body.push('<div class="ma-tip__hint">'
                + (props.manual ? '직접 추가한 일정입니다.'
                                : '누르면 이 일정이 나온 메일이 열립니다.')
                + '</div>');
      const html = '<div class="ma-tip__head">'
        + `<span class="ma-tip__dot" style="background:${info.event.backgroundColor}"></span>`
        + `<span class="ma-tip__title">${escapeHtml(props.clean || info.event.title)}</span>`
        + `</div><div class="ma-tip__row">${escapeHtml(props.kind || '')}</div>`
        + body.join('');
      info.el.addEventListener('mouseenter', () => showTip(info.el, html));
      info.el.addEventListener('mouseleave', hideTip);
      if (props.missed) info.el.style.textDecoration = 'line-through';
    },
    eventClick: (info) => {
      info.jsEvent.preventDefault();
      hideTip();
      // A manual entry has no mail to open, and emitting its key would send the
      // 메일 화면 looking for an id no mail can carry.
      if (info.event.extendedProps.manual) return;
      emitEvent('mail-open', info.event.id);
    },
  });
  calendar.render();
};
</script>
""".replace('__KIND_ICONS__', json.dumps(KIND_ICONS, ensure_ascii=False))


def build(directory, config, token, hub=None, services=None, config_path=None,
          can_start=True, updater=None):
    """Register the pages. The token gates the data, not the static assets."""
    import subprocess

    from fastapi import Request
    from nicegui import app, run as nicerun, ui

    from . import services as helpers, update
    from .report import remember_secret
    from .updater import (FAILED, READY, WORKING, checked_text, consequence, offer_line,
                          progress_text, recheck_text)
    from .worker import connect_detail

    config_path = config_path or (directory / 'config.json')

    # Served from the package, never from a CDN: this app has to work offline.
    app.add_static_files('/vendor', str(vendor_path()))

    def allowed(request):
        return not token or request.query_params.get('t') == token

    # 업데이트 ---------------------------------------------------------

    def collecting():
        return hub is not None and hub.running()

    def bump():
        """Say that a screen changed something another screen shows.

        Every page reads the database on its own timer, so nothing is *lost* without
        this — it only decides whether the 대시보드 catches a deletion in a second or
        in five. Called by everything that writes: 처리 상태, 삭제, 다시 분석, 초안,
        할 일. See Hub.touch().
        """
        if hub is not None:
            hub.touch()

    def revision():
        return hub.revision if hub is not None else 0

    # 초안 만들기 ------------------------------------------------------

    def draft_maker(view, opened, after):
        """말투와 방향을 골라 답변 초안을 만드는 칸. 메일 상세와 초안 화면이 같은 것을 쓴다.

        Analysis no longer writes a draft, so this is where one comes from. It carries
        its own heading and its own fold, exactly as the sidebar's 접기 sits on the
        sidebar: the control and the thing it opens are then one gesture, and a page
        that wants a maker places one element rather than three.
        """
        picks = draft_picks(app.storage.user.get('draft'))
        busy = {'now': False}
        state = {'open': bool(opened)}
        live = ' is-open' if opened else ''

        def fold():
            """A class swap and nothing else — see run_strip()."""
            state['open'] = not state['open']
            for element in (shut, mark):
                element.classes(add='is-open') if state['open'] \
                    else element.classes(remove='is-open')

        async def make():
            if busy['now']:
                return
            if not view['analysed']:
                ui.notify('분석이 끝난 뒤에 초안을 만들 수 있습니다.')
                return
            busy['now'] = True
            go.props('loading')
            ui.notify('초안을 만드는 중입니다. 분석이 돌고 있으면 그 뒤에 처리됩니다.')
            try:
                subject, text = await nicerun.io_bound(
                    helpers.draft, draft_input(view), picks['tone'], picks['way'], config)
            except Exception as exc:
                go.props(remove='loading')
                ui.notify(str(exc) or f'초안을 만들지 못했습니다: {type(exc).__name__}')
                return
            finally:
                busy['now'] = False
            if not store(directory).set_reply_draft(view['id'], subject, text):
                go.props(remove='loading')
                ui.notify('분석이 끝난 뒤에 초안을 만들 수 있습니다.')
                return
            # The next mail is usually answered in the same voice as this one.
            app.storage.user['draft'] = dict(picks)
            bump()      # 검토 전 초안 is a 대시보드 card, and this is what moves it
            ui.notify('새 초안으로 바꿨습니다. 고쳐 쓰면 그대로 저장됩니다.'
                      if view['draft'] else '초안을 만들었습니다. 고쳐 쓰면 그대로 저장됩니다.')
            after()     # refresh last: it deletes the button this is running in

        with ui.element('div').classes('ma-maker'):
            with ui.element('div').classes('ma-maker__top'):
                ui.icon('auto_awesome').style(f'color:{BRAND};font-size:16px')
                ui.label('AI로 초안 만들기').classes('ma-maker__title')
                ui.label('말투와 방향을 고르세요.').classes('ma-meta__item')
                ui.space()
                opener = ui.button(on_click=fold).props('flat dense round size=sm') \
                    .style(f'color:{MUTED}').tooltip('펼치기·접기')
                with opener:
                    mark = ui.icon('expand_more').classes('ma-fold__mark' + live)
            shut = ui.element('div').classes('ma-fold' + live)
            with shut, ui.element('div'):
                for key, label, options in (('tone', '말투', DRAFT_TONES),
                                            ('way', '방향', DRAFT_WAYS)):
                    with ui.element('div').classes('ma-maker__row'):
                        ui.label(label).classes('ma-maker__label')
                        ui.toggle({name: name for name in options}, value=picks[key],
                                  on_change=lambda event, which=key:
                                  picks.__setitem__(which, event.value)) \
                            .props('no-caps dense unelevated toggle-color=primary') \
                            .classes('ma-seg')
                with ui.element('div').classes('ma-maker__row'):
                    ui.label(DRAFT_NOTE).classes('ma-meta__item')
                    ui.space()
                    # The label is the warning: there is no other place in this app
                    # where text a person typed is replaced, so the button has to say
                    # so before it is pressed rather than after.
                    go = ui.button('초안 새로 만들기' if view['draft'] else '초안 만들기',
                                   icon='auto_awesome', on_click=make) \
                        .props('unelevated dense no-caps')
                    if view['draft']:
                        go.tooltip('지금 쓰인 초안을 새로 만든 초안으로 바꿉니다.')

    # 사이드바 ---------------------------------------------------------

    # One meter for the process, not one per page: it holds the reading every screen
    # draws, and its own gate is what keeps ten open tabs to one read between them.
    # `services` decides whether it can ask at all, so off Windows — or with no Codex
    # on PATH — the chip is simply absent rather than reading 0%.
    meter = UsageMeter((services or {}).get('codex_usage'))

    chrome_cache = {'counts': {}, 'revision': None, 'at': 0.0}

    def note_counts(rows, todos):
        """Feed the sidebar from rows a page has already read.

        The 대시보드 reads the whole mailbox every REFRESH_SECONDS for its own cards, and
        the badges want exactly those rows — letting chrome() query again would be the
        second page() per tick that home()'s read() was inlined to avoid.
        """
        chrome_cache.update(counts=nav_counts(rows, todos), revision=revision(),
                            at=time.monotonic())

    def chrome():
        """The three things the shell repaints, on one call: badges, chip, offer.

        Every page draws the sidebar, so the counts are re-read only when a screen says
        something moved (Hub.revision, the same integer 대시보드 follows) or when they
        are older than one beat — the worker collects without touching that integer, so
        a revision that has not moved is not by itself proof that nothing has.
        """
        if (chrome_cache['revision'] != revision()
                or time.monotonic() - chrome_cache['at'] >= REFRESH_SECONDS):
            account = account_of(config)
            opened = store(directory)
            note_counts(list(opened.page(account)) if account else [],
                        list(opened.todos(account)) if account else [])
        return {'counts': chrome_cache['counts'],
                'state': hub.state() if hub is not None else None,
                'offer': updater.offer if updater is not None else None,
                # Already read and shaped: the chip repaints from the meter's last
                # answer, and the asking is watch_usage()'s own slow beat.
                'usage': meter.view()}

    async def watch_update():
        """The shell's slow beat. None when there is nothing new, which is most beats.

        `Updater.watch()` is not forced, so `update.check()`'s own six-hour gate decides
        whether this costs a request at all; every other beat returns without leaving the
        machine. Several open pages each run this, and the stamp check() writes is what
        keeps them from all asking at once.
        """
        if updater is None:
            return None
        return await nicerun.io_bound(updater.watch)

    async def watch_usage():
        """The 사용량 beat. Blocking, so it goes through io_bound like every other call.

        `Meter.watch()` has its own gate and its own lock, so several open pages beating
        together cost one read between them — the same arrangement that keeps the update
        check from asking GitHub once per page.
        """
        return await nicerun.io_bound(meter.watch)

    def page_shell(current):
        """The shell every page opens with, wired to this build's hub and updater."""
        return shell(current, token, chrome,
                     watch_update if updater is not None else None,
                     watch_usage if meter.read is not None else None)

    async def install():
        """Stop collecting, then bring the server down so main() can run Setup.

        The installer replaces the files this process is running from, so it cannot
        start until the window is gone — and main()'s finally is what starts it, after
        the mutex is closed. Everything here does is get out of the way in that order.
        """
        if hub is not None:
            hub.halt()
        for _ in range(600):        # the window waited the same way, for the same reason
            if not collecting():
                break
            await asyncio.sleep(1.0)
        app.shutdown()

    def update_body(refresh, close=None):
        """Whichever of the four states the updater is in. Built by both screens."""
        if updater is None:
            empty('이 환경에서는 업데이트를 확인할 수 없습니다.')
            return
        if updater.state == READY:
            with ui.element('div').classes('ma-wait'):
                ui.spinner(size='sm')
                ui.label('설치를 시작합니다. 잠시 후 메일 도우미가 다시 열립니다…') \
                    .style(f'color:{INK};font-size:13px')
            return
        if updater.state == WORKING:
            total = updater.progress.get('total') or 0
            done = updater.progress.get('done') or 0
            ui.label('설치 파일을 내려받는 중입니다. 창을 닫지 마세요.') \
                .style(f'color:{INK};font-size:13px;margin-bottom:8px')
            ui.linear_progress(done / total if total else 0, show_value=False) \
                .classes('ma-progress').props('size=6px rounded'
                                              + ('' if total else ' indeterminate'))
            with ui.element('div').classes('ma-row').style('border:none'):
                ui.label(progress_text(updater.progress)).classes('ma-meta__item')
                ui.space()
                ui.button('취소', on_click=updater.stop).props('flat dense no-caps')
            return
        if updater.offer is None:
            empty(f'최신 버전입니다. (현재 {__version__})')
            return
        ui.label(f"새 버전 {updater.offer['version']}이(가) 나왔습니다.") \
            .style(f'color:{INK};font-size:16px;font-weight:700;letter-spacing:-.01em')
        ui.label(offer_line(updater.offer, __version__)).classes('ma-meta__item') \
            .style('margin:2px 0 12px')
        ui.label('변경 내용').classes('ma-field')
        with ui.element('div').classes('ma-sunken ma-scroll ma-log') \
                .style('max-height:190px;width:100%;margin-bottom:10px'):
            ui.label(updater.offer['notes']).style('white-space:pre-wrap;'
                                                   'overflow-wrap:anywhere')
        ui.label(consequence(collecting())).classes('ma-lede').style('margin-bottom:0')
        if updater.state == FAILED and updater.message:
            with ui.element('div').classes('ma-alert').style('margin-top:10px'):
                ui.icon('error_outline').style('font-size:16px')
                ui.label(updater.message).style('font-size:12px')
        elif updater.message:
            ui.label(updater.message).classes('ma-meta__item').style('margin-top:8px')

        async def take():
            # The dialog stays open on purpose: it is where the progress bar is, and
            # closing it here left the user staring at a page with nothing happening.
            updater.asked = True
            refresh()
            done = await nicerun.io_bound(updater.take, collecting())
            refresh()
            if done:
                await install()

        def skip():
            version = updater.skip()
            updater.asked = True
            if close:
                close()
            refresh()
            ui.notify(f'{version} 버전은 건너뜁니다. 다음 버전이 나오면 다시 알려 드립니다.')

        def later():
            updater.asked = True
            if close:
                close()
            ui.notify('실행 화면의 업데이트에서 언제든 설치할 수 있습니다.')

        with ui.element('div').style('display:flex;gap:6px;margin-top:14px;flex-wrap:wrap'):
            ui.button('지금 업데이트', icon='system_update_alt', on_click=take) \
                .props('unelevated dense no-caps')
            ui.button('나중에', on_click=later).props('outline dense no-caps')
            ui.space()
            ui.button('이 버전 건너뛰기', on_click=skip) \
                .props('flat dense no-caps text-color=grey-7')

    def update_panel():
        """The 업데이트 card's body — the same one on 실행 and 설정, so they cannot drift.

        Whatever state the updater is in, then when it was last asked, then the way to
        ask again. 지금 확인 says which of the four things happened rather than going
        quiet: '새 버전이 없습니다' and '물어보지 못했습니다' are not the same sentence.
        """
        def say(text):
            """The one line under the card. It lives outside `block` on purpose.

            Saying '확인하는 중입니다…' by rebuilding the panel deletes the 지금 확인
            button — and the handler is running in that button's slot, so after the
            await ui.notify() could not find the client any more: the RuntimeError
            killed the rest of the handler and the waiting line stayed on screen for
            ever. A label that is only re-texted deletes nothing.
            """
            note.set_text(text)
            note.set_visibility(bool(text))

        def unskip():
            update.remember(config_path, {'update_skip': ''})
            config['update_skip'] = ''
            say('건너뛴 버전을 초기화했습니다. 지금 확인을 누르면 다시 알려 드립니다.')
            block.refresh()

        async def recheck():
            say('확인하는 중입니다…')
            result = await nicerun.io_bound(updater.recheck)
            say(recheck_text(result, updater.offer))
            ui.notify(recheck_text(result, updater.offer))
            # Last, because this is what deletes the button we were clicked from.
            block.refresh()

        @ui.refreshable
        def block():
            update_body(block.refresh)
            if updater is None:
                return
            with ui.element('div').classes('ma-row').style('border:none;padding:10px 0 0'):
                ui.label(checked_text(config.get('update_checked'))).classes('ma-meta__item')
                ui.space()
                if updater.state not in (WORKING, READY):
                    ui.button('지금 확인', icon='refresh', on_click=recheck) \
                        .props('flat dense no-caps text-color=secondary')
            skipped = config.get('update_skip') or ''
            if skipped:
                with ui.element('div').classes('ma-row') \
                        .style('border:none;padding:6px 0 0'):
                    ui.label(f'건너뛴 버전: {skipped}').classes('ma-meta__item')
                    ui.space()
                    ui.button('다시 알림 받기', icon='notifications_active', on_click=unskip) \
                        .props('flat dense no-caps')

        block()
        note = ui.label('').classes('ma-meta__item').style('margin-top:2px')
        note.set_visibility(False)
        if updater is not None:
            # Built here rather than inside a handler: refresh() deletes the slot a
            # timer would take its client from, and the RuntimeError kills the handler.
            ui.timer(0.4, lambda: block.refresh() if updater.state == WORKING else None)
        return block

    def usage_panel():
        """The Codex 사용량 card on 실행: the header chip, spelled out.

        The chip in the band is one number and a tooltip; this is where the two windows,
        the reset clock and the plan are written down, because a tooltip is not something
        a reader finds when they are looking for why analysis stopped. 지금 확인 says the
        in-between things with set_text() on a label outside the refreshable, exactly as
        업데이트's own 지금 확인 learned to.
        """
        def say(text):
            note.set_text(text)
            note.set_visibility(bool(text))

        async def again():
            if meter.read is None:
                ui.notify('이 환경에서는 Codex 사용량을 확인할 수 없습니다.')
                return
            say('확인하는 중입니다…')
            # look(), not watch(): a button press is the one read that must not be
            # answered out of the cache it is pressed to get past.
            await nicerun.io_bound(meter.look)
            say('')                  # whatever happened is in the card's own lines now
            ui.notify(meter.message or '사용량을 다시 읽었습니다.')
            block.refresh()          # last: it deletes the button this is running in

        @ui.refreshable
        def block():
            view = meter.view()
            if meter.read is None:
                empty('이 환경에서는 Codex 사용량을 확인할 수 없습니다.')
                return
            if not view['known']:
                empty('아직 사용량을 읽지 못했습니다. 아래 버튼으로 확인하세요.')
            else:
                tone = USAGE_TONES.get(view['level'], MUTED)
                with ui.element('div').classes('ma-row').style('border:none;padding:0'):
                    ui.label(view['text']).style(f'color:{tone};font-size:15px;'
                                                 'font-weight:700;letter-spacing:-.01em')
                # color=None, or nicegui adds Quasar's own text-primary class and the
                # bar is brand blue however urgent the number in front of it is.
                ui.linear_progress(view['percent'] / 100, show_value=False, color=None) \
                    .classes('ma-progress ma-progress--inline') \
                    .props('size=6px rounded').style(f'color:{tone}')
                for line in view['lines']:
                    ui.label(line).classes('ma-meta__item').style('margin:2px 0 0 2px')
            with ui.element('div').classes('ma-row').style('border:none;padding:10px 0 0'):
                ui.label('분석·상담·브리핑이 모두 이 한도를 함께 씁니다.') \
                    .classes('ma-meta__item')
                ui.space()
                ui.button('지금 확인', icon='refresh', on_click=again) \
                    .props('flat dense no-caps text-color=secondary')

        block()
        note = ui.label('').classes('ma-meta__item').style('margin-top:2px')
        note.set_visibility(False)
        return block

    # 대시보드 ---------------------------------------------------------

    @ui.page('/')
    def home(request: Request):
        if not allowed(request):
            refused()
            return

        window = {'days': DUE_DAYS}
        # 수집 기록 is shut until somebody opens it, and stays open across the beat that
        # rebuilds the card — which is the only reason this is not a local of run_strip.
        log_open = {'open': False}
        latest = {}

        def read():
            """One read per tick: every block and chart below sees the same rows.

            Inlined rather than snapshot(): the kanban tally needs the same rows and a
            second page() every five seconds would read the whole mailbox twice.
            """
            account = account_of(config)
            rows = list(store(directory).page(account)) if account else []
            todos = list(store(directory).todos(account)) if account else []
            events = list(store(directory).events(account)) if account else []
            today = date.today()
            latest['data'] = overview(rows, today, within=window['days'], events=events)
            latest['todo'] = board_counts(rows, todos)
            latest['today'] = today_rows(latest['data']['events'], today,
                                         latest['data']['handled'])
            latest['trend'] = trend(store(directory), account, today)
            latest['brief'] = briefing_card(store(directory), account, today)
            memos = memo_views()
            latest['pinned'] = pinned_notes(memos)
            latest['memos'] = sum(1 for view in memos if view['pinned'])
            note_counts(rows, todos)
            return latest['data']

        def ask_briefing():
            """A booking, like 다시 분석: the worker owns the Codex slot, not this page."""
            account = account_of(config)
            if not account:
                ui.notify('설정에서 메일 주소를 먼저 입력하세요.')
                return
            store(directory).set_meta('briefing_ask:' + account, '1')
            if collecting():
                hub.wake()
            ui.notify(briefing_text(collecting()))

        @ui.refreshable
        def brief_block():
            briefing_body(latest['brief'], date.today(), token,
                          briefing_empty(collecting(), latest['data']['total'],
                                         datetime.now().hour),
                          ask_briefing)

        @ui.refreshable
        def kpi_row():
            cards(latest['data'], token)

        @ui.refreshable
        def today_block():
            today_panel(latest['today'], date.today(), token)

        @ui.refreshable
        def wait_block():
            waiting_panel(latest['data']['reply_wait'], token, tick)

        @ui.refreshable
        def deadline_block():
            deadlines(latest['data'], date.today(), token, tick)

        @ui.refreshable
        def todo_block():
            todo_tally(latest['todo'], token)

        @ui.refreshable
        def memo_block():
            pinned_panel(latest['pinned'], token, latest['memos'])

        @ui.refreshable
        def run_block():
            if hub is not None:
                run_strip(run_summary(hub, directory, config), token, log_open)

        @ui.refreshable
        def summary_row():
            ui.label(summary_line(latest['data'])).classes('ma-meta__item')

        def tick(ident, done):
            """One mail can carry several deadlines; all its rows move together.

            The tick writes the mail's own 처리 상태 — the same field the kanban moves —
            so the two screens cannot disagree about what is finished. A 직접 추가한
            일정 has no mail to write to and keeps the same flag on its own row.
            """
            manual = event_row_id(ident)
            if manual is None:
                store(directory).set_handled(ident, HANDLED if done else '')
            else:
                store(directory).set_event_handled(manual, HANDLED if done else '')
            bump()
            read()
            # The kanban's middle column is this same field, so its tally moves too,
            # and a handled mail leaves 오늘 일정 the way it leaves the calendar.
            for block in (kpi_row, today_block, wait_block, deadline_block, todo_block,
                          summary_row):
                block.refresh()

        @ui.refreshable
        def offer_body():
            update_body(offer_body.refresh, close=lambda: dialog.close())

        async def look():
            """The window checked 600ms after opening; update.check() gates the rest."""
            if updater is None or updater.asked or updater.offer is not None:
                return
            if await nicerun.io_bound(updater.look):
                offer_body.refresh()
                dialog.open()

        read()
        with page_shell('/'):
            dialog = ui.dialog()
            with dialog, card().style('max-width:620px'):
                offer_body()
            if updater is not None:
                ui.timer(0.6, look, once=True)
                # Only while a download is running; idle this costs one dead callback.
                ui.timer(0.4, lambda: offer_body.refresh()
                         if updater.state == WORKING else None)
                if updater.waiting() and not updater.asked:
                    dialog.open()
            with briefing_frame():
                brief_block()
            with ui.element('div').style('margin-top:14px'):
                kpi_row()
            with ui.element('div').classes('ma-split').style('margin-top:14px'):
                with ui.element('div').classes('ma-stack'):
                    with card('일별 처리량', 'show_chart'):
                        daily = chart(trend_option(latest['trend']), 196)
                    with grid(minimum=240):
                        with card('메일 종류', 'label', note=BAR_NOTE):
                            kinds = bar_link(chart(bar_option(
                                [(name, latest['data']['categories'][name])
                                 for name in CATEGORIES]),
                                24 * len(CATEGORIES) + 12, cap=460),
                                'category', CATEGORIES, token)
                        with card('우선순위', 'flag', note=BAR_NOTE):
                            ranks = bar_link(chart(bar_option(
                                [(name, latest['data']['priorities'][name])
                                 for name, _ in PRIORITIES], STATUS),
                                24 * len(PRIORITIES) + 12, cap=460),
                                'priority', PRIORITY_NAMES, token)
                with ui.element('div').classes('ma-stack'):
                    today_block()
                    wait_block()
                    with card(flush=True):
                        with ui.element('div').classes('ma-head') \
                                .style('padding:16px 18px 0;margin-bottom:10px'):
                            ui.icon('event_busy').style(f'color:{MUTED};font-size:17px')
                            ui.label('마감 임박 · 지난 마감').classes('ma-head__title')
                            ui.space()
                            ui.toggle({days: f'{days}일' for days in WINDOWS},
                                      value=window['days'],
                                      on_change=lambda event: pick(event.value)) \
                                .props('no-caps dense unelevated toggle-color=primary') \
                                .classes('ma-seg')
                        deadline_block()
                    todo_block()
                    memo_block()
                    run_block()
            with ui.element('div').style('margin-top:14px'):
                summary_row()

            def paint():
                data = read()
                for block in (brief_block, kpi_row, today_block, wait_block,
                              deadline_block, todo_block, memo_block, run_block,
                              summary_row):
                    block.refresh()
                for element, option in (
                        (daily, trend_option(latest['trend'])),
                        (kinds, bar_option([(name, data['categories'][name])
                                            for name in CATEGORIES])),
                        (ranks, bar_option([(name, data['priorities'][name])
                                            for name, _ in PRIORITIES], STATUS))):
                    # Update in place rather than rebuild: a refreshable would drop the
                    # canvas and replay the entry animation every five seconds.
                    element.options.clear()
                    element.options.update(option)
                    element.update()

            def pick(days):
                window['days'] = days
                read()
                kpi_row.refresh()
                deadline_block.refresh()

            seen = {'revision': revision(), 'at': 0.0}

            def beat():
                """Repaint on the slow beat, or at once when another screen wrote.

                Deleting a mail, ticking a 마감, moving a card: none of those happen on
                this page, and before this the numbers here sat still for up to five
                seconds afterwards — long enough to read as 'the 대시보드 does not
                follow the 메일 화면'. The check itself is one integer compare.
                """
                moved = revision()
                elapsed = time.monotonic() - seen['at']
                if moved == seen['revision'] and elapsed < REFRESH_SECONDS:
                    return
                seen['revision'], seen['at'] = moved, time.monotonic()
                paint()

            seen['at'] = time.monotonic()
            ui.timer(LIVE_SECONDS, beat)

    # 메일 -------------------------------------------------------------

    @ui.page('/mail')
    def mail(request: Request):
        if not allowed(request):
            refused()
            return
        saved = dict(app.storage.user.get('list') or {})
        # A card or bar link arrives with its own filter; it wins over what was
        # remembered. A filter the link did not name is left as it was: 공지 clicked
        # from the 대시보드 keeps the 미처리 the reader chose here a minute ago.
        for key in ('state', 'sort', 'query', 'category', 'priority'):
            if key in request.query_params:
                saved[key] = request.query_params[key]
                saved['page'] = 0
        if 'desc' in request.query_params:
            # Read here rather than left to list_state(), which ends bool(value): a
            # link's '0' is a non-empty string and every one of those is True. 답장
            # 대기 is the one card whose list has to open oldest-first, because its
            # own hint names the oldest row.
            saved['desc'] = request.query_params['desc'] not in ('0', 'false')
            saved['page'] = 0
        state = list_state(saved)
        opened = request.query_params.get('id')
        if opened:
            state['selected'] = opened
        wanted = {'ids': []}        # what the 삭제 confirmation is standing over
        touched = {'now': False}    # did the open mail change anything the list shows
        fresh = {'id': None}        # the memo 메모 붙이기 just made, to open with the caret
        # 원문 or 한글 번역, and whether Codex is being waited on. Outside panel(),
        # which is rebuilt by 처리 완료, 다시 분석 and every memo written on the mail.
        reading = {'tab': '', 'busy': False}
        drafting = {'open': False}  # has the reader opened 초안 만들기 for this mail
        # The table is rebuilt by the second now, and a rebuild is what clears q-table's
        # checkboxes. `live` carries the ticked ids and the last painted signature
        # across that rebuild, so a poll that finds nothing new does nothing at all.
        live = {'table': None, 'signature': None}

        def remember(**changes):
            state.update(changes)
            app.storage.user['list'] = dict(state)

        def choose(ident):
            """Open the mail over the list.

            The table is deliberately left alone: rebuilding fifty rows behind a dialog
            that covers them costs a query and shows nobody anything. What the detail
            changed is painted when it closes.
            """
            remember(selected=ident)
            # A different mail is a different reading: the tab is chosen again below
            # from what that mail is, not carried over from the one just closed.
            reading['tab'] = ''
            drafting['open'] = False
            panel.refresh()
            detail.open()

        def edit(**changes):
            remember(page=0, **changes)
            rows.refresh()

        def sort_by(key):
            """A header click. The list is paged in sqlite, so this is a new query."""
            order, desc = next_sort(state['sort'], state['desc'], key)
            remember(sort=order, desc=desc, page=0)
            rows.refresh()

        def ask_delete(ids):
            if not ids:
                ui.notify('선택된 메일이 없습니다.')
                return
            wanted['ids'] = list(ids)
            warn.set_text(f'메일 {len(ids)}건을 지웁니다. 분석 결과와 답변 초안, 이 메일에 대한 '
                          '상담 기록도 함께 사라지며 되돌릴 수 없습니다. 직접 쓴 메모는 '
                          '지워지지 않고 메모 화면에 남습니다. 이미 엑셀에 반영된 행은 '
                          '그대로 남고, 서버에서 같은 메일을 다시 가져오지도 않습니다.')
            confirm.open()

        def erase():
            ids, wanted['ids'] = list(wanted['ids']), []
            confirm.close()
            if not ids:
                return
            store(directory).delete(ids)
            bump()
            if state['selected'] in ids:
                remember(selected=None)
                detail.close()
            rows.refresh()
            ui.notify(f'{len(ids)}건을 삭제했습니다.')

        def fetch_once():
            """One POP3 round, right now, on whichever thread io_bound gave us.

            A Store of its own because a sqlite connection cannot cross threads, and
            connect_detail() because the server's refusal can echo what was sent to it.
            """
            password = services['read_password'](config['email'])
            remember_secret(password)
            opened_store = Store(directory / 'mail.db')
            try:
                message = helpers.fetch_mail(config, opened_store, password)
            except Exception as exc:
                raise RuntimeError(connect_detail(exc, password)) from None
            else:
                opened_store.set_meta('last_fetch:' + account_of(config), now())
                return message
            finally:
                opened_store.db.close()

        async def collect():
            """'지금 가져오기' — connect now instead of waiting out 확인 간격.

            A running collector is woken rather than raced: one POP3 session per
            account, and the worker is the one that also analyses and exports.
            """
            if hub is not None and hub.running():
                hub.wake()
                ui.notify('지금 확인을 요청했습니다. 수집이 끝나면 목록에 나타납니다.')
                return
            if not services or not services.get('read_password'):
                ui.notify('이 환경에서는 메일을 가져올 수 없습니다. Windows에서 실행하세요.')
                return
            # Only what a fetch needs: this button never opens the workbook, so an
            # unset 엑셀 파일 must not stand between the user and their mail.
            errors = [text for key, text in field_errors(config).items()
                      if key in ('email', 'host', 'port')]
            if errors:
                ui.notify('설정을 먼저 확인하세요: ' + ' '.join(errors))
                return
            fetch.props(add='loading')
            try:
                message = await nicerun.io_bound(fetch_once)
            except LookupError:
                ui.notify('메일 전용 비밀번호가 없습니다. 설정에서 입력하세요.')
            except Exception as exc:
                ui.notify(f'가져오지 못했습니다: {exc}')
                if hub is not None:
                    hub.log(f'지금 가져오기 실패: {exc}')
            else:
                said = collect_text(message, hub is not None and hub.running())
                if hub is not None:
                    hub.log('지금 가져오기: ' + said)
                ui.notify(said)
                rows.refresh()
            finally:
                fetch.props(remove='loading')

        @ui.refreshable
        def rows():
            data = listing(directory, config, state)
            if data['page'] != state['page']:
                remember(page=data['page'])
            live['signature'] = list_signature(data)
            # What was ticked before this rebuild. mark(), again() and erase() clear
            # the selection themselves, so only a repaint the *user* did not ask for
            # — a poll that found new mail — restores anything here.
            previous = live['table']
            kept = {row['id'] for row in previous.selected} if previous is not None else set()
            # Widths are fixed except 제목, which takes the rest; max-width:0 is the
            # trick that makes a flexible table cell ellipsize instead of overflowing.
            # 수신 is wide enough for '09-14 13:39' on one line: at 104px it wrapped,
            # and a wrapped date made every row in the list two lines tall.
            widths = {'received': 'width:118px', 'sender': 'width:200px',
                      'subject': 'max-width:0;overflow:hidden;text-overflow:ellipsis;'
                                 'white-space:nowrap',
                      'category': 'width:96px', 'priority': 'width:90px', 'state': 'width:96px'}
            columns = [{'name': key, 'label': label, 'field': key, 'align': 'left',
                        'style': widths[key], 'headerStyle': widths[key].split(';')[0]}
                       for key, label in LIST_FIELDS]
            # 무엇에 대한 메일인지: the analysis without opening the mail. Last, and
            # nameless, because it is a button rather than a column of values — and it
            # is not in LIST_FIELDS, which is the set of things the list sorts by.
            columns.append({'name': TIP_COLUMN, 'label': '', 'field': TIP_COLUMN,
                            'align': 'center', 'style': 'width:46px',
                            'headerStyle': 'width:46px'})
            with card(flush=True):
                with ui.element('div').style('width:100%;overflow-x:auto'):
                    table = ui.table(columns=columns, rows=data['rows'], row_key='id',
                                     selection='multiple').classes('w-full ma-table')
                    # selected-rows-label is a function, and Quasar's default writes
                    # '1 record selected.' in English under a Korean table. A ':' prop
                    # is evaluated in the browser, which is the only way to pass one.
                    table.props('flat wrap-cells=false '
                                ''':selected-rows-label="n => n + '건 선택됨'"''')
                    for key, _ in LIST_FIELDS:
                        table.add_slot(f'header-cell-{key}',
                                       header_cell(key, state['sort'], state['desc']))
                    table.on('sortby', lambda event: sort_by(clicked_key(event.args)))
                    # Quasar's own selection checkbox lets the click reach the row, and
                    # ticking a box would open the mail. .stop is the whole of the slot.
                    table.add_slot('body-selection',
                                   '<q-checkbox v-model="props.selected" dense'
                                   ' @click.stop></q-checkbox>')
                    table.add_slot('body-cell-received',
                                   '<q-td :props="props"><span style="color:%s">'
                                   '{{ props.value }}</span></q-td>' % SUBTLE)
                    table.add_slot('body-cell-subject',
                                   '<q-td :props="props"><span style="color:%s">'
                                   '{{ props.value }}</span></q-td>' % INK)
                    table.add_slot('body-cell-category', tag_cell({}))
                    table.add_slot('body-cell-priority', tag_cell(STATUS))
                    table.add_slot('body-cell-state',
                                   tag_cell(STATE_TONES, failure=css_color(URGENT)))
                    table.add_slot(f'body-cell-{TIP_COLUMN}', tip_cell())
                    table.on('rowClick', lambda event: choose(event.args[1]['id']))
                    live['table'] = table
                    if kept:
                        # .selected is the same list object as the 'selected' prop, so
                        # it is extended, never rebound.
                        table.selected.extend(row for row in data['rows']
                                              if row['id'] in kept)

                def picked():
                    return [row['id'] for row in table.selected]

                def mark(value):
                    ids = picked()
                    if not ids:
                        ui.notify('선택된 메일이 없습니다.')
                        return
                    store(directory).set_handled_many(ids, value)
                    bump()
                    table.selected.clear()
                    rows.refresh()
                    ui.notify(f"{len(ids)}건을 {'처리 완료' if value else '미처리'}로 바꿨습니다.")

                def again():
                    ids = picked()
                    if not ids:
                        ui.notify('선택된 메일이 없습니다.')
                        return
                    # reset() skips whatever Codex is holding right now, and says how
                    # many it skipped: clearing a result that is already on its way
                    # would be overwritten, and the request would vanish silently.
                    asked = store(directory).reset(ids, reanalyze=True)
                    bump()
                    if hub is not None:
                        hub.wake()
                    table.selected.clear()
                    rows.refresh()
                    ui.notify(reanalyze_text(asked, len(ids), running=collecting()))

                def step(delta):
                    remember(page=max(0, min(data['pages'] - 1, state['page'] + delta)))
                    rows.refresh()

                with ui.element('div').classes('ma-foot'):
                    ui.label(f"{data['first']}–{data['last']} / {data['total']}건"
                             if data['total'] else '조건에 맞는 메일이 없습니다.') \
                        .classes('ma-meta__item')
                    ui.button('처리 완료', icon='done', on_click=lambda: mark(HANDLED)) \
                        .props('flat dense no-caps text-color=secondary')
                    ui.button('미처리로', icon='undo', on_click=lambda: mark('')) \
                        .props('flat dense no-caps text-color=secondary')
                    ui.button('다시 분석', icon='refresh', on_click=again) \
                        .props('flat dense no-caps text-color=secondary').tooltip(REANALYZE_TIP)
                    ui.button('삭제', icon='delete_outline',
                              on_click=lambda: ask_delete(picked())) \
                        .props('flat dense no-caps text-color=negative')
                    ui.space()
                    ui.button(icon='chevron_left', on_click=lambda: step(-1)) \
                        .props('flat dense round').set_enabled(data['page'] > 0)
                    ui.label(f"{data['page'] + 1} / {data['pages']}").classes('ma-meta__item')
                    ui.button(icon='chevron_right', on_click=lambda: step(1)) \
                        .props('flat dense round').set_enabled(data['page'] + 1 < data['pages'])

        @ui.refreshable
        def panel():
            """The mail itself, as the dialog's contents.

            Whatever it changes — 처리 상태, a re-analysis, a deletion — is painted into
            the list when the dialog closes, not while it is covering the list.
            """
            ident = state['selected']
            row = store(directory).detail(ident) if ident else None
            if row is None:
                with card().classes('ma-modal'):
                    empty('메일을 찾을 수 없습니다. 목록에서 다시 선택하세요.')
                return
            view = detail_view(row)
            if not reading['tab']:
                # 번역이 있고 원문이 한국어가 아니면 번역부터: that mail was opened to
                # be read, and the original is one press away either way.
                reading['tab'] = ('korean' if view['translated'] and view['foreign']
                                  else 'origin')

            def copy():
                # Not awaited: nicegui 3's clipboard.write() returns None, and
                # `await None` raised inside the handler — which killed the ui.notify
                # after it, so the button copied and said nothing.
                ui.clipboard.write(draft.value or '')
                ui.notify('답변 초안을 클립보드에 복사했습니다.')

            def toggle():
                store(directory).set_handled(view['id'], '' if view['handled'] else HANDLED)
                bump()
                touched['now'] = True
                panel.refresh()

            def retry():
                asked = store(directory).reset([view['id']], reanalyze=view['analysed'])
                bump()
                if hub is not None:
                    hub.wake()
                touched['now'] = True
                panel.refresh()
                ui.notify(reanalyze_text(asked, 1, running=collecting()))

            def unhide():
                store(directory).set_todo_hidden(view['id'], False)
                bump()
                panel.refresh()
                ui.notify('할 일 판에 다시 올렸습니다.')

            def showing():
                """The text the panel is showing, which is what 복사 copies."""
                return (view['translated'] if reading['tab'] == 'korean'
                        else view['body'])

            def take():
                # Not awaited: nicegui 3's clipboard.write() returns None.
                ui.clipboard.write(showing())
                ui.notify(('번역을 클립보드에 복사했습니다.' if reading['tab'] == 'korean'
                           else '원문을 클립보드에 복사했습니다.'))

            def made():
                """A draft has just been written into the analysis. Keep the pickers
                open: the second press is usually another voice for the same mail."""
                drafting['open'] = True
                touched['now'] = True       # 상태 카드와 초안 수가 함께 움직인다
                panel.refresh()

            def turn(tab):
                """Which of the two readings is on screen. Visibility, never a refresh:
                the other one is already built and a rebuild would cost the scroll."""
                reading['tab'] = tab if view['translated'] else 'origin'
                origin.set_visibility(reading['tab'] != 'korean')
                korean.set_visibility(reading['tab'] == 'korean')
                # set_text on an element that outlives the switch, never a refresh.
                told.set_text('번역 복사' if reading['tab'] == 'korean' else '원문 복사')

            async def render():
                """Ask Codex for the Korean once, and keep it with the mail.

                On the same one slot as analysis, so this may wait minutes behind a
                mail being analysed — which is what the notice says out loud rather
                than leaving a button spinning for no stated reason.
                """
                if reading['busy']:
                    return
                reading['busy'] = True
                ask.props('loading')
                ui.notify('번역을 요청했습니다. 분석이 돌고 있으면 그 뒤에 처리됩니다.')
                try:
                    language, text = await nicerun.io_bound(
                        helpers.translate, view['body'], view['subject'], config)
                except Exception as exc:
                    ask.props(remove='loading')
                    ui.notify(str(exc) or f'번역하지 못했습니다: {type(exc).__name__}')
                    return
                finally:
                    reading['busy'] = False
                store(directory).set_translation(view['id'], language, text)
                reading['tab'] = 'korean'
                # Last, as ever: refresh() deletes the button this handler is running
                # in, and anything under ui. after it has no client left to resolve.
                # 영어 원문을, not 영어을(를): a particle chosen by hand is one that
                # is wrong half the time, and the noun after it takes the same one.
                ui.notify(f'{language} 원문을 한국어로 옮겼습니다.'.lstrip())
                panel.refresh()

            def attach():
                """A blank memo on this mail, with the caret already in it.

                The row is made first so the card can appear at once; a blank one
                nobody typed into is swept the next time a wall is rebuilt, which is
                what delete_empty_notes() is for.
                """
                account = account_of(config)
                if not account:
                    ui.notify('설정에서 메일 주소를 먼저 저장하세요.')
                    return
                store(directory).delete_empty_notes(account)
                fresh['id'] = store(directory).add_note(account, color=DEFAULT_NOTE_COLOR,
                                                        mail_id=view['id'])
                bump()
                panel.refresh()

            with card().classes('ma-modal ma-scroll'):
                with ui.element('div').style('display:flex;gap:14px;align-items:flex-start;'
                                             'flex-wrap:wrap'):
                    with ui.element('div').style('flex:1 1 320px;min-width:0'):
                        ui.label(view['subject']) \
                            .style(f'color:{INK};font-size:16.5px;font-weight:700;'
                                   'letter-spacing:-.01em;line-height:1.4')
                        with ui.element('div').classes('ma-meta').style('margin-top:8px'):
                            ui.label(view['sender']).classes('ma-meta__item')
                            ui.label(view['received']).classes('ma-meta__item')
                            tag(view['category'])
                            tag(view['priority'], STATUS.get(view['priority']),
                                soft_of(STATUS.get(view['priority'])))
                            tag(view['state'], STATE_TONES.get(view['state'], SUBTLE),
                                soft_of(STATE_TONES.get(view['state'], SUBTLE)))
                    with ui.element('div').style('display:flex;gap:5px;flex-wrap:wrap;'
                                                 'align-items:center'):
                        ui.button('미처리로' if view['handled'] else '처리 완료',
                                  icon='undo' if view['handled'] else 'done',
                                  on_click=toggle).props('unelevated dense no-caps')
                        link = mailto(view['sender'], view['reply_subject'], view['draft'])
                        if link:
                            ui.button('답장 열기', icon='reply',
                                      on_click=lambda url=link: ui.navigate.to(url)) \
                                .props('outline dense no-caps')
                        ui.button('상담', icon='forum',
                                  on_click=lambda: ui.navigate.to(
                                      href('/chat', token, id=view['id']))) \
                            .props('flat dense no-caps')
                        ui.button('다시 분석', icon='refresh', on_click=retry) \
                            .props('flat dense no-caps').tooltip(REANALYZE_TIP)
                        # Only for a mail whose card was swept off the board: this is
                        # the one place that can put it back, so it has to be here.
                        if view['todo_hidden']:
                            ui.button('할 일 판에 다시 올리기', icon='playlist_add',
                                      on_click=unhide).props('flat dense no-caps')
                        ui.button(icon='delete_outline',
                                  on_click=lambda: ask_delete([view['id']])) \
                            .props('flat dense round text-color=negative').tooltip('삭제')
                        ui.button(icon='close', on_click=detail.close) \
                            .props('flat dense round').tooltip('닫기')
                if view['error']:
                    with ui.element('div').classes('ma-alert').style('margin-top:12px'):
                        ui.icon('error_outline').style('font-size:16px')
                        ui.label(view['error']).style('font-size:12px')
                if view['clipped']:
                    # Amber, not red: the analysis below is real, it just did not see
                    # all of the mail — which the reader has to be told before reading it.
                    with ui.element('div').classes('ma-alert ma-alert--warn') \
                            .style('margin-top:12px'):
                        ui.icon('content_cut').style('font-size:16px')
                        ui.label(clipped_note(view['clipped'])).style('font-size:12px')
                # Context before content: a mail's analysis reads differently once you
                # know it is the fourth turn of a negotiation. One query, and only for
                # a mail whose conversation actually has another mail in it.
                thread_strip(thread_view(
                    store(directory).thread_rows(account_of(config), row['thread']),
                    view['id']), token)
                with grid(minimum=320, gap=22).style('margin-top:16px;align-items:start'):
                    with ui.element('div'):
                        with ui.element('div').classes('ma-head') \
                                .style(f'margin-bottom:8px;min-height:{HEAD_ROW}px'):
                            ui.label('분석 결과').classes('ma-head__title')
                        if not view['analysed']:
                            empty('아직 분석되지 않았습니다.')
                        for part in analysis_blocks(view):
                            accent = f" ma-part--{part['accent']}" if part['accent'] else ''
                            with ui.element('div').classes('ma-part' + accent):
                                with ui.element('div').classes('ma-part__head'):
                                    ui.icon(part['icon']) \
                                        .style(f"color:{PART_TONES[part['accent']]}")
                                    ui.label(part['label']).classes('ma-part__label')
                                ui.label(part['text']).classes('ma-part__body')
                                if part['key'] == 'action':
                                    # 다음 행동 is already a card on the 할 일 판 — the
                                    # board grows one for every analysed mail that
                                    # produced one — so this says where it went rather
                                    # than offering to put it there a second time.
                                    with ui.element('div').classes('ma-part__foot'):
                                        if view['todo_hidden']:
                                            ui.label('할 일 판에서 치운 항목입니다.') \
                                                .classes('ma-meta__item')
                                        else:
                                            ui.link('할 일 판에서 보기',
                                                    href('/todo', token)) \
                                                .classes('ma-part__link')
                        for event in detail_events(view['events']):
                            tone = css_color(COLORS[event['kind']])
                            with ui.element('div').classes('ma-part') \
                                    .style(f'border-left-color:{tone}'):
                                with ui.element('div').classes('ma-part__head'):
                                    ui.icon(KIND_ICONS[event['kind']]).style(f'color:{tone}')
                                    ui.label(event['title']).classes('ma-part__label')
                                    ui.space()
                                    tag(event['kind'], tone, soft_of(tone))
                                with ui.element('div').classes('ma-meta'):
                                    ui.label(f"시작 {event['start']}").classes('ma-meta__item')
                                    ui.label(f"마감 {event['deadline']}") \
                                        .classes('ma-meta__item')
                                if event['evidence']:
                                    ui.label(f"근거: {event['evidence']}") \
                                        .classes('ma-meta__item') \
                                        .style('display:block;margin-top:4px;'
                                               'white-space:pre-wrap')
                                with ui.element('div').classes('ma-part__foot'):
                                    if event['on_calendar']:
                                        ui.link('달력에서 보기', href('/calendar', token)) \
                                            .classes('ma-part__link')
                                    else:
                                        # The one thing the calendar cannot say: this
                                        # event carries no date it could be drawn on.
                                        ui.label('읽을 수 있는 날짜가 없어 달력에는 '
                                                 '올라가지 않습니다. 일정 화면에서 직접 '
                                                 '추가할 수 있습니다.') \
                                            .classes('ma-meta__item')
                        if view['attachments']:
                            with ui.element('div').classes('ma-part'):
                                with ui.element('div').classes('ma-part__head'):
                                    ui.icon('attach_file').style(f'color:{MUTED}')
                                    ui.label('첨부').classes('ma-part__label')
                                with ui.element('div').classes('ma-meta'):
                                    for name in view['attachments']:
                                        with ui.element('div').classes('ma-tag') \
                                                .style(tag_style(SUBTLE) + ';gap:3px'):
                                            ui.icon('attach_file').style('font-size:13px')
                                            ui.label(name)
                    with ui.element('div'):
                        # Same row height as 분석 결과 beside it: this one holds a
                        # button and that one does not, and a heading 3px lower than
                        # its neighbour is the sort of thing only a screenshot shows.
                        with ui.element('div').classes('ma-head') \
                                .style(f'margin-bottom:8px;min-height:{HEAD_ROW}px'):
                            ui.label('원문').classes('ma-head__title')
                            ui.space()
                            if view['translated']:
                                # Two readings of one mail, so a toggle and not a
                                # second panel below: the numbers are in the original
                                # and nobody should have to scroll past one to reach it.
                                ui.toggle({'origin': '원문', 'korean': '한글 번역'},
                                          value=reading['tab'],
                                          on_change=lambda event: turn(event.value)) \
                                    .props('no-caps dense unelevated '
                                           'toggle-color=primary').classes('ma-seg')
                                ask = ui.button(icon='autorenew', on_click=render) \
                                    .props('flat dense round size=sm') \
                                    .style(f'color:{MUTED}').tooltip('다시 번역')
                            else:
                                # Offered for every mail and pressed for a few: the
                                # tone is what says which this one looks like, because
                                # 한국어가 아닙니다 is a guess and this is not.
                                ask = ui.button('한글 번역', icon='translate',
                                                on_click=render) \
                                    .props(('outline' if view['foreign'] else 'flat')
                                           + ' dense no-caps').tooltip(TRANSLATE_TIP)
                            # One button for both readings: it copies whichever is on
                            # screen, which is the only thing 복사 can mean here.
                            with ui.button(icon='content_copy', on_click=take) \
                                    .props('flat dense round size=sm') \
                                    .style(f'color:{MUTED}'):
                                told = ui.tooltip('')
                        # display:block on purpose: a bare div inherits a centring flex
                        # layout here, which parks a short body in the middle of the box.
                        origin = ui.element('div').classes('ma-sunken ma-scroll') \
                            .style('display:block;max-height:340px;width:100%')
                        with origin:
                            body_panel(view['body'])
                        korean = ui.element('div').classes('ma-sunken ma-scroll') \
                            .style('display:block;max-height:340px;width:100%')
                        if view['translated']:
                            with korean:
                                ui.label(translated_note(view['language'])) \
                                    .classes('ma-meta__item') \
                                    .style('display:block;margin-bottom:8px')
                                body_panel(view['translated'])
                        turn(reading['tab'])
                        # The memos written about this mail. They live in the same
                        # table as the 메모 화면's and are edited the same way here —
                        # this is what a memo can do that a 할 일 card cannot, so it
                        # has to be reachable from the mail itself.
                        section('메모')
                        memos = memo_views(view['id'])
                        with ui.element('div').style('display:grid;gap:8px'):
                            memo_cards(memos, panel.refresh, fresh.pop('id', None),
                                       linked=False)
                        if not memos:
                            empty('이 메일에 붙인 메모가 없습니다.')
                        ui.button('이 메일에 메모 붙이기', icon='add', on_click=attach) \
                            .props('outline dense no-caps').style('margin-top:8px')
                with ui.element('div').classes('ma-head').style('margin:18px 0 6px'):
                    ui.label('답변 초안').classes('ma-head__title')
                    if view['analysed']:
                        # The analysis answers this and nothing on screen showed it:
                        # an empty draft box looks the same whether Codex judged no
                        # answer was wanted or simply had nothing to say.
                        if view['reply_needed']:
                            tag('답장 필요', BRAND, BRAND_SOFT)
                        else:
                            tag('답장 불필요', MUTED, SUNKEN)
                    ui.label('입력을 멈추면 자동 저장됩니다.').classes('ma-meta__item')
                    ui.space()
                    ui.button('초안 복사', icon='content_copy', on_click=copy) \
                        .props('flat dense no-caps')
                # Open when there is nothing in the box, and kept open after a
                # generation: the first draft is the one most often asked for again
                # in another voice, and shutting it would hide the pickers to do it.
                if view['analysed']:
                    draft_maker(view, drafting['open'] or not view['draft'], made)
                draft = ui.textarea(value=view['draft']).classes('w-full')
                draft.props('outlined autogrow debounce=800')

                def save(text):
                    store(directory).set_draft(view['id'], text or '')
                    bump()      # 검토 전 초안 is a 대시보드 card, and this is what moves it

                draft.on_value_change(lambda event: save(event.value))

        with page_shell('/mail'):
            with card().style('padding:12px 14px;margin-bottom:14px'):
                with ui.element('div').style('display:flex;gap:8px;align-items:center;'
                                             'flex-wrap:wrap'):
                    search = ui.input(placeholder='제목·발신자·본문·요약 검색',
                                      value=state['query'])
                    search.props('dense outlined clearable debounce=400') \
                        .style('flex:1 1 260px')
                    with search.add_slot('prepend'):
                        ui.icon('search').style(f'color:{MUTED};font-size:18px')
                    search.on_value_change(lambda event: edit(query=event.value or ''))
                    picker = ui.select({value: value or '전체 상태' for value in STATES},
                                       value=state['state'])
                    picker.props('dense outlined options-dense').style('min-width:140px')
                    picker.on_value_change(lambda event: edit(state=event.value or ''))
                    # 종류 and 우선순위 are here, and not only on the link the 대시보드
                    # sends, because a filter that is remembered has to be visible: a
                    # list quietly narrowed to 공지 since yesterday is a list that has
                    # lost mail, and nothing on screen would say why.
                    for key, label, names in BAR_FILTERS:
                        narrow = ui.select({'': f'전체 {label}',
                                            **{name: name for name in names}},
                                           value=state[key])
                        narrow.props('dense outlined options-dense') \
                            .style('min-width:130px')
                        narrow.on_value_change(
                            lambda event, key=key: edit(**{key: event.value or ''}))
                    ui.space()
                    fetch = ui.button('지금 가져오기', icon='cloud_download', on_click=collect) \
                        .props('unelevated dense no-caps') \
                        .tooltip('확인 간격을 기다리지 않고 지금 한 번 연결합니다')
                    ui.button(icon='sync', on_click=lambda: rows.refresh()) \
                        .props('flat dense round').tooltip('목록 새로 고침')
            confirm = ui.dialog()
            with confirm, card().style('max-width:440px'):
                ui.label('메일을 삭제할까요?') \
                    .style(f'color:{INK};font-size:15px;font-weight:700')
                warn = ui.label('').classes('ma-lede').style('margin:8px 0 14px')
                with ui.element('div').style('display:flex;gap:6px;justify-content:flex-end'):
                    ui.button('취소', on_click=confirm.close).props('flat dense no-caps')
                    ui.button('삭제', icon='delete_outline', on_click=erase) \
                        .props('unelevated dense no-caps color=negative')
            detail = ui.dialog()
            with detail:
                panel()

            def left(event):
                """Repaint the list when the detail closes, and only if it changed it.

                Rebuilding the table is also what clears the checkboxes, so a mail
                opened to be *read* must not cost the user the selection they made.
                """
                if event.value or not touched['now']:
                    return
                touched['now'] = False
                rows.refresh()

            detail.on_value_change(left)
            rows()

            def watch():
                """Follow the worker: a mail that starts analysing says so here.

                Not while a dialog is over the list — rebuilding rows nobody can see
                costs a query and would pull the ground out from under the open mail.
                The signature is what keeps this to one query on a quiet second: a
                repaint clears the checkboxes, so it must happen only when something
                the table actually draws has changed.
                """
                if detail.value or confirm.value:
                    return
                if list_signature(listing(directory, config, state)) != live['signature']:
                    rows.refresh()

            ui.timer(LIVE_SECONDS, watch)
            if opened:
                # A link from 대시보드·일정·할 일·초안 points at one mail; open that mail.
                detail.open()

    # 일정 -------------------------------------------------------------

    @ui.page('/calendar')
    def calendar(request: Request):
        if not allowed(request):
            refused()
            return
        ui.on('mail-open', lambda event: ui.navigate.to(href('/mail', token, id=event.args)))
        account = account_of(config)

        def reload():
            """The grid is a payload baked into the page, so a change is a fresh load.

            ui.navigate.to of this same path is the reload: the alternative is holding
            a handle on a calendar that lives entirely in the browser.
            """
            bump()
            ui.navigate.to(href('/calendar', token))

        data = snapshot(directory, config)
        payload = calendar_events(data['events'], date.today(), data['handled'])
        ui.add_head_html(CALENDAR_SCRIPT)
        # The payload rides in the page rather than arriving by run_javascript: that
        # call depends on the client being connected, and a calendar that renders on
        # load has nothing to gain from the round trip.
        ui.add_body_html(CALENDAR_SETUP + '<script>window.mailCalendar('
                         + json.dumps(payload, ensure_ascii=False) + ');</script>')
        with page_shell('/calendar'):
            with card():
                with ui.element('div').classes('ma-head'):
                    ui.label('').props('id=calendar-title') \
                        .style(f'color:{INK};font-size:15px;font-weight:700')
                    ui.space()
                    for kind in ('마감', '시작', '확인 필요'):
                        with ui.element('div').classes('ma-meta').style('gap:4px'):
                            # The same icon the events carry, so the legend explains
                            # what is actually on the grid rather than a second key.
                            ui.icon(KIND_ICONS[kind]) \
                                .style(f'color:{css_color(COLORS[kind])};font-size:15px')
                            ui.label(kind).classes('ma-meta__item')
                    # Not every entry has one now: 직접 추가한 일정 open nothing, and the
                    # tooltip is what says so on the entry itself.
                    ui.label('메일에서 나온 일정을 누르면 그 메일이 열립니다.') \
                        .classes('ma-meta__item')
                ui.element('div').props('id=calendar').style('width:100%')
            with ui.element('div').style('margin-top:14px'):
                manual_panel(directory, account, token, reload)
            missed = past_due(data['events'], date.today(), handled=data['handled'],
                              limit=UPCOMING * 3)
            if missed:
                with ui.element('div').style('margin-top:14px'):
                    with card(f'지난 마감 {len(missed)}건', 'error_outline'):
                        for day, entry in reversed(missed):
                            with ui.element('div').classes('ma-row'):
                                ui.icon(KIND_ICONS[entry.kind]) \
                                    .style(f'color:{css_color(COLORS[entry.kind])};'
                                           'font-size:14px;flex:none')
                                ui.label(day.isoformat()).classes('ma-meta__item') \
                                    .style('min-width:86px')
                                ui.link(plain_title(entry.label),
                                        href('/mail', token, id=entry.mail_id)) \
                                    .style(f"color:{css_color(URGENT)};font-size:13px;"
                                           'text-decoration:none;font-weight:500')
                                ui.space()
                                ui.label(describe(day, date.today())).classes('ma-meta__item')

    # 할 일 -----------------------------------------------------------

    @ui.page('/todo')
    def todo_page(request: Request):
        if not allowed(request):
            refused()
            return
        account = account_of(config)

        def apply_move(kind, key, state):
            if kind == 'mail':
                store(directory).set_handled(key, state)
            else:
                store(directory).set_todo_state(int(key), state)
            bump()
            lanes.refresh()

        def move(card_row, state):
            apply_move(card_row['kind'], card_row['key'], state)

        def dropped(event, state):
            landed = drag_drop(event.args, state)
            if landed:
                apply_move(landed[0], landed[1], state)

        def drop(card_row):
            """Take a card off the board. A mail card is swept, never deleted.

            The 할 일 판 grows a card for every analysed mail that produced a next
            action, and before this there was no way to be finished with one except to
            mark the mail 완료 — which is a different sentence. Sweeping hides the card
            and leaves the mail exactly as it was; 메일 화면 puts it back.
            """
            if card_row['kind'] == 'mail':
                store(directory).set_todo_hidden(card_row['key'], True)
                said = '할 일 판에서 치웠습니다. 메일은 그대로 있고, 메일 화면에서 다시 올릴 수 있습니다.'
            else:
                store(directory).delete_todo(card_row['key'])
                said = '할 일을 지웠습니다.'
            bump()
            lanes.refresh()
            ui.notify(said)

        def add():
            text = (entry.value or '').strip()
            if not text:
                ui.notify('할 일을 입력하세요.')
                return
            if not account:
                ui.notify('설정을 먼저 저장하세요.')
                return
            store(directory).add_todo(account, text, (due.value or '').strip())
            bump()
            entry.set_value('')
            due.set_value('')
            lanes.refresh()

        @ui.refreshable
        def lanes():
            rows = list(store(directory).page(account)) if account else []
            todos = list(store(directory).todos(account)) if account else []
            data = board(rows, todos)
            with grid(minimum=280).style('align-items:start'):
                for index, (state, label) in enumerate(COLUMNS):
                    here = data[state]
                    lane = card(flush=True)
                    # The whole lane is the drop target, head included: a card aimed at
                    # the title bar is aimed at that column.
                    lane.on('dragover', js_handler=DRAG_OVER)
                    lane.on('dragleave', js_handler=DRAG_LEAVE)
                    lane.on('drop', lambda event, s=state: dropped(event, s),
                            js_handler=DRAG_DROP)
                    with lane:
                        with ui.element('div').classes('ma-lane__head'):
                            ui.element('div').classes('ma-dot') \
                                .style(f'background:{LANE_TONES[state]}')
                            ui.label(label).classes('ma-head__title')
                            ui.label(str(len(here))).classes('ma-count')
                        with ui.element('div').classes('ma-lane'):
                            if not here:
                                ui.label('여기로 끌어다 놓기').classes('ma-drop')
                            for item in here:
                                note = ui.element('div').classes('ma-note') \
                                    .props('draggable=true') \
                                    .style(f'border-left-color:{LANE_TONES[state]}')
                                note.on('dragstart',
                                        js_handler=drag_start(drag_payload(state, item)))
                                note.on('dragend', js_handler=DRAG_END)
                                with note:
                                    ui.icon('drag_indicator').classes('ma-note__grip') \
                                        .style('font-size:15px')
                                    ui.label(item['text']).classes('ma-note__text').style(
                                        f'color:{INK};font-size:13px;white-space:pre-wrap;'
                                        'line-height:1.55')
                                    with ui.element('div').classes('ma-meta') \
                                            .style('margin-top:6px'):
                                        ui.label(item['note']).classes('ma-meta__item')
                                        if item['due']:
                                            tag(f"마감 {item['due']}")
                                        if item['priority'] == '긴급':
                                            tag('긴급', css_color(URGENT),
                                                soft_of(css_color(URGENT)))
                                    with ui.element('div').classes('ma-note__foot'):
                                        if index:
                                            ui.button(icon='chevron_left',
                                                      on_click=lambda c=item,
                                                      s=COLUMNS[index - 1][0]: move(c, s)) \
                                                .props('flat dense round size=sm color=grey-7') \
                                                .tooltip(f'{COLUMNS[index - 1][1]}(으)로')
                                        if index + 1 < len(COLUMNS):
                                            ui.button(icon='chevron_right',
                                                      on_click=lambda c=item,
                                                      s=COLUMNS[index + 1][0]: move(c, s)) \
                                                .props('flat dense round size=sm color=grey-7') \
                                                .tooltip(f'{COLUMNS[index + 1][1]}(으)로')
                                        ui.space()
                                        if item['kind'] == 'mail':
                                            ui.button(icon='mail',
                                                      on_click=lambda c=item: ui.navigate.to(
                                                          href('/mail', token, id=c['key']))) \
                                                .props('flat dense round size=sm color=grey-7') \
                                                .tooltip('메일 열기')
                                        # Both kinds can leave the board; only a manual
                                        # todo is actually deleted by it.
                                        ui.button(icon='delete_outline',
                                                  on_click=lambda c=item: drop(c)) \
                                            .props('flat dense round size=sm color=grey-7') \
                                            .tooltip('할 일 판에서 치우기 (메일은 남습니다)'
                                                     if item['kind'] == 'mail' else '삭제')

        with page_shell('/todo'):
            ui.label('메일에서 나온 다음 행동과, 직접 적은 할 일을 한 판에 둡니다. '
                     '카드를 끌어다 옮기거나 화살표 버튼을 눌러 옮길 수 있고, '
                     '메일 카드를 옮기면 그 메일의 처리 상태가 함께 바뀝니다. '
                     '휴지통은 카드를 판에서 치웁니다 — 메일 카드는 메일을 지우지 않습니다.') \
                .classes('ma-lede')
            with card().style('padding:12px 14px;margin-bottom:14px'):
                with ui.element('div').style('display:flex;gap:8px;flex-wrap:wrap'):
                    entry = ui.input(placeholder='할 일 추가').props('dense outlined') \
                        .style('flex:1 1 260px')
                    due = ui.input(placeholder='마감 (2026-09-30, 선택)') \
                        .props('dense outlined').style('min-width:200px')
                    ui.button('추가', icon='add', on_click=add) \
                        .props('unelevated dense no-caps')
            lanes()

    # 초안 -----------------------------------------------------------

    @ui.page('/drafts')
    def drafts_page(request: Request):
        if not allowed(request):
            refused()
            return
        account = account_of(config)
        chosen = {'id': request.query_params.get('id')}
        live = {'ids': ()}
        made = {'open': False}      # 초안 만들기 opened for the mail being looked at

        def queue_ids():
            rows = list(store(directory).unedited_drafts(account)) if account else []
            return tuple(row['id'] for row in review_queue(rows))

        @ui.refreshable
        def queue():
            rows = list(store(directory).unedited_drafts(account)) if account else []
            data = draft_view(rows, chosen['id'])
            live['ids'] = tuple(item['id'] for item in data['queue'])
            if not data['queue']:
                with card():
                    empty('답장을 기다리는 메일이 없습니다. 답변이 필요한 메일이 분석되면 '
                          '여기에 모입니다.')
                return
            view = data['current']

            def step(delta):
                ids = [item['id'] for item in data['queue']]
                chosen['id'] = ids[(data['index'] + delta) % len(ids)]
                made['open'] = False        # another mail, another draft
                queue.refresh()

            def copy():
                # See the 메일 detail's copy(): clipboard.write() is not awaitable.
                ui.clipboard.write(draft.value or '')
                ui.notify('초안을 복사했습니다.')

            def done():
                store(directory).set_handled(view['id'], HANDLED)
                bump()
                chosen['id'] = None
                made['open'] = False
                queue.refresh()

            def remade():
                """A draft was just written for this mail. The queue keeps it — only a
                draft a *person* has touched leaves — so this is a repaint, not a step."""
                made['open'] = True
                queue.refresh()

            with card():
                with ui.element('div').classes('ma-head').style('margin-bottom:8px'):
                    ui.label(f"{data['index'] + 1} / {len(data['queue'])}건") \
                        .classes('ma-tag').style(tag_style(BRAND))
                    ui.space()
                    ui.button(icon='chevron_left', on_click=lambda: step(-1)) \
                        .props('flat dense round').tooltip('이전')
                    ui.button(icon='chevron_right', on_click=lambda: step(1)) \
                        .props('flat dense round').tooltip('다음')
                ui.label(view['subject']) \
                    .style(f'color:{INK};font-size:16.5px;font-weight:700;line-height:1.4')
                with ui.element('div').classes('ma-meta').style('margin:8px 0 4px'):
                    ui.label(view['sender']).classes('ma-meta__item')
                    ui.label(view['received']).classes('ma-meta__item')
                    tag(view['category'])
                    tag(view['priority'], STATUS.get(view['priority']),
                        soft_of(STATUS.get(view['priority'])))
                if view['requests']:
                    ui.label('요청사항').classes('ma-field')
                    ui.label(view['requests']).style(f'color:{INK};font-size:13px;'
                                                     'white-space:pre-wrap;line-height:1.65')
                with ui.element('div').classes('ma-head').style('margin:16px 0 6px'):
                    ui.label('답변 초안').classes('ma-head__title')
                    ui.label('입력을 멈추면 자동 저장됩니다.').classes('ma-meta__item')
                # The queue is 답장이 필요한 메일 now rather than 이미 쓰인 초안, so the
                # maker belongs here as much as it does in the mail itself.
                draft_maker(view, made['open'] or not view['draft'], remade)
                draft = ui.textarea(value=view['draft']).classes('w-full')
                draft.props('outlined autogrow debounce=800')

                def save(text):
                    store(directory).set_draft(view['id'], text or '')
                    bump()

                draft.on_value_change(lambda event: save(event.value))
                with ui.element('div').style('display:flex;gap:6px;flex-wrap:wrap;'
                                             'margin-top:12px;align-items:center'):
                    ui.button('처리 완료', icon='done', on_click=done) \
                        .props('unelevated dense no-caps')
                    ui.button('초안 복사', icon='content_copy', on_click=copy) \
                        .props('outline dense no-caps')
                    link = mailto(view['sender'], view['reply_subject'], view['draft'])
                    if link:
                        ui.button('답장 열기', icon='reply',
                                  on_click=lambda url=link: ui.navigate.to(url)) \
                            .props('flat dense no-caps')
                    ui.space()
                    ui.link('메일에서 보기', href('/mail', token, id=view['id'])) \
                        .classes('ma-meta__item').style('text-decoration:none')

        with page_shell('/drafts'):
            ui.label('답장이 필요한데 아직 손대지 않은 메일입니다. 말투와 방향을 골라 초안을 '
                     '만들고, 고쳐 쓰면 자동 저장되며 저장한 메일은 목록에서 빠집니다.') \
                .classes('ma-lede')
            with ui.element('div').classes('ma-alert ma-alert--warn') \
                    .style('margin-bottom:12px') as notice:
                ui.icon('mark_email_unread').style('font-size:16px')
                arrived = ui.label('').style('font-size:12px')
                ui.space()
                ui.button('새로 고침', icon='refresh',
                          on_click=lambda: queue.refresh()).props('flat dense no-caps')
            notice.set_visibility(False)
            queue()

            def watch():
                """Say that the queue moved; never rebuild it from under the typist.

                The one thing on this page is a textarea somebody is in the middle of,
                and refresh() would replace it with whatever the database last saw —
                exactly the edit-eating the window's draft_baseline exists to stop. So
                a new draft is announced and the reader decides when to take it.
                """
                ids = queue_ids()
                if ids == live['ids']:
                    notice.set_visibility(False)
                    return
                if not live['ids']:
                    queue.refresh()     # nothing on screen to lose
                    return
                fresh = len([ident for ident in ids if ident not in live['ids']])
                arrived.set_text(f'새 초안 {fresh}건이 도착했습니다.' if fresh
                                 else '검토할 초안 목록이 바뀌었습니다.')
                notice.set_visibility(True)

            ui.timer(REFRESH_SECONDS, watch)

    # 메모 -----------------------------------------------------------

    def memo_views(mail_id=None):
        """Every memo the screen wants, with the subjects of the mail they belong to."""
        account = account_of(config)
        if not account:
            return []
        opened = store(directory)
        rows = list(opened.notes(account, mail_id))
        return note_views(rows, opened.mail_subjects(row['mail_id'] for row in rows))

    def memo_cards(views, rebuild, fresh=None, on_save=None, linked=True):
        """The memo card, wherever it is drawn.

        Both the 메모 화면 and the 메일 detail draw this; what differs between them is
        only what a rebuild means, so that is the one thing passed in. `linked` is
        False inside a mail's own detail, where naming the mail every card already
        belongs to is the same thing said once per card.
        """
        def save(ident, text):
            store(directory).set_note_text(ident, text or '')
            bump()
            if on_save is not None:
                on_save()

        def recolor(ident, key, paper, swatches):
            """Repaint the paper in place — a rebuild here would take the text with it.

            Colour is the one property of a memo that changes nothing about where the
            card belongs, so it is also the one that need not cost a rebuild.
            """
            store(directory).set_note_color(ident, key)
            bump()
            ground, edge = note_tone(key)
            paper.style(f'background:{ground};border-color:{edge}55;'
                        f'border-left-color:{edge}')
            for name, swatch in swatches.items():
                swatch.classes(add='is-live') if name == key \
                    else swatch.classes(remove='is-live')
            if on_save is not None:
                on_save()

        def pin(ident, pinned):
            """고정 moves the card between the two walls, so this one does rebuild.

            Whatever was being typed is safe: clicking the button blurs the textarea
            first, and Quasar flushes a debounced input on blur — the save lands
            before this handler runs.
            """
            store(directory).set_note_pinned(ident, pinned)
            bump()
            rebuild()

        def drop(ident):
            store(directory).delete_note(ident)
            bump()
            # Before the rebuild, never after: refresh() deletes the slot this handler
            # is running in, and ui.notify then has no client to resolve.
            ui.notify('메모를 지웠습니다.')
            rebuild()

        for view in views:
            ident = view['id']
            with memo_paper(view) as paper:
                area = ui.textarea(value=view['text'], placeholder='메모를 적으세요') \
                    .classes('w-full')
                area.props('borderless autogrow dense debounce=600'
                           + (' autofocus' if ident == fresh else ''))
                area.on_value_change(lambda event, i=ident: save(i, event.value))
                if linked:
                    memo_mail_link(view, token)
                with ui.element('div').classes('ma-memo__foot'):
                    ui.label(view['when']).classes('ma-memo__when')
                    with ui.element('div').classes('ma-memo__acts'):
                        swatches = {}
                        with ui.element('div').classes('ma-memo__pal'):
                            for key, name, ground, _ in NOTE_COLORS:
                                swatch = ui.element('button').classes('ma-sw') \
                                    .style(f'background:{ground}').tooltip(name)
                                swatch.on('click', lambda _, i=ident, k=key, p=paper,
                                          s=swatches: recolor(i, k, p, s))
                                swatches[key] = swatch
                        chosen = view['color'] or DEFAULT_NOTE_COLOR
                        if chosen in swatches:
                            swatches[chosen].classes(add='is-live')
                        ui.button(icon='push_pin',
                                  on_click=lambda i=ident, p=view['pinned']: pin(i, not p)) \
                            .props('flat dense round size=sm'
                                   + (' color=primary' if view['pinned'] else ' color=grey-7')) \
                            .tooltip('고정 해제' if view['pinned'] else '고정')
                        ui.button(icon='close', on_click=lambda i=ident: drop(i)) \
                            .props('flat dense round size=sm color=grey-7').tooltip('삭제')

    @ui.page('/memo')
    def memo_page(request: Request):
        if not allowed(request):
            refused()
            return
        account = account_of(config)
        state = {'filter': 'all'}
        # `fresh` is the memo 새 메모 just made, so its card can open with the caret in
        # it; `signature` is what the tick compares against. Both die with the page.
        live = {'signature': (), 'fresh': None}

        def sweep():
            """Drop blank memos before a rebuild. See Store.delete_empty_notes()."""
            if account:
                store(directory).delete_empty_notes(account)

        def resync():
            """Take the wall's own writes out of what watch() is watching for.

            Without this the notice would fire on the user's own keystroke: the
            signature carries the text, and a save they just made is a change.
            """
            live['signature'] = note_signature(memo_views())

        def rebuild():
            sweep()
            board.refresh()

        def add():
            if not account:
                ui.notify('설정에서 메일 주소를 먼저 저장하세요.')
                return
            sweep()
            live['fresh'] = store(directory).add_note(account, color=DEFAULT_NOTE_COLOR)
            bump()
            # A new memo is neither pinned nor attached, so a filter that hides it
            # would answer 새 메모 with an unchanged screen.
            state['filter'] = 'all'
            board.refresh()

        def pick(kind):
            state['filter'] = kind or 'all'
            sweep()
            board.refresh()

        def wall_head(title, icon):
            with ui.element('div').classes('ma-wall__head'):
                ui.icon(icon).style(f'color:{MUTED}')
                ui.label(title)
                ui.element('div').classes('ma-wall__rule')

        @ui.refreshable
        def board():
            views = memo_views()
            live['signature'] = note_signature(views)
            counts = note_tally(views)
            data = note_wall(views, state['filter'])
            with ui.element('div').style('display:flex;gap:8px;align-items:center;'
                                         'flex-wrap:wrap;margin-bottom:14px'):
                ui.button('새 메모', icon='add', on_click=add) \
                    .props('unelevated dense no-caps')
                ui.space()
                ui.toggle({kind: f'{name} {counts[kind]}' for kind, name in NOTE_FILTERS},
                          value=state['filter'],
                          on_change=lambda event: pick(event.value)) \
                    .props('no-caps dense unelevated toggle-color=primary').classes('ma-seg')
            if not views:
                with card():
                    empty('아직 메모가 없습니다. 메일도 일정도 할 일도 아닌 것 — 전화번호, '
                          '양식이 있는 자리, 회의 중에 흘려 적은 한 줄을 여기에 둡니다.')
            elif not data['pinned'] and not data['rest']:
                with card():
                    empty('이 조건에 맞는 메모가 없습니다.')
            for title, icon, key in (('고정', 'push_pin', 'pinned'),
                                     ('메모', 'sticky_note_2', 'rest')):
                if not data[key]:
                    continue
                wall_head(title, icon)
                # align-items:start, so a memo is as tall as what is written on it,
                # and auto-fill rather than grid()'s own auto-fit: fit collapses the
                # empty tracks, so a 고정 wall holding one memo drew it a metre wide
                # above a 메모 wall of ordinary cards.
                with grid(minimum=250, gap=12) \
                        .style('align-items:start;margin-bottom:16px;'
                               'grid-template-columns:repeat(auto-fill,minmax(250px,1fr))'):
                    memo_cards(data[key], rebuild, live['fresh'], resync)
            live['fresh'] = None

        with page_shell('/memo'):
            ui.label('메일도 일정도 할 일도 아닌 것을 적어두는 곳입니다. 입력을 멈추면 저장되고, '
                     '메일에 붙인 메모는 그 메일을 열 때 같이 나옵니다. 압정을 누르면 '
                     '대시보드에도 올라옵니다.').classes('ma-lede')
            with ui.element('div').classes('ma-alert ma-alert--warn') \
                    .style('margin-bottom:12px') as notice:
                ui.icon('sticky_note_2').style('font-size:16px')
                arrived = ui.label('').style('font-size:12px')
                ui.space()
                ui.button('새로 고침', icon='refresh', on_click=lambda: reload()) \
                    .props('flat dense no-caps')
            notice.set_visibility(False)

            def reload():
                notice.set_visibility(False)
                board.refresh()

            board()

            def watch():
                """Say that a memo changed elsewhere; never rebuild it from under the typist.

                Every card here is a textarea somebody may be inside, which is the
                same reason the 초안 화면 announces rather than refreshes. It is not
                theory: the 메일 detail writes memos into this same table, and so does
                a second window open on another screen.
                """
                views = memo_views()
                signature = note_signature(views)
                if signature == live['signature']:
                    notice.set_visibility(False)
                    return
                if not live['signature']:
                    board.refresh()     # an empty wall has nothing to lose
                    return
                arrived.set_text('다른 화면에서 메모가 바뀌었습니다.')
                notice.set_visibility(True)

            ui.timer(REFRESH_SECONDS, watch)

    # 상담 -----------------------------------------------------------

    @ui.page('/chat')
    def chat_page(request: Request):
        if not allowed(request):
            refused()
            return
        account = account_of(config)
        # `id` is the room key: a mail's id, a 새 대화's own key, or '' for 일반 상담.
        state = {'room': request.query_params.get('id') or '', 'query': ''}
        busy = {'now': False}

        def mail_of(key):
            """The mail a room hangs off, or None — a 새 대화 has no mail behind it."""
            if not key or key.startswith(ROOM_MARK):
                return None
            return store(directory).detail(key)

        def current():
            """The open room as the sidebar shapes it, so both say the same name."""
            rows = room_rows(store(directory).rooms(account), state['room']) if account else []
            for row in rows:
                if row['open']:
                    return rows, row
            # A key nobody has written to yet is not in Store.rooms(), which reads the
            # turns — and a thread opened from 메일 has none until the first question.
            # Its name is the mail's own subject, or the title read 제목 없음 for the
            # whole of the first answer.
            mail = mail_of(state['room'])
            entry = room_rows([{'key': state['room'], 'at': '', 'turns': 0,
                                'name': mail['subject'] if mail is not None else ''}],
                              state['room'])[0]
            # And it goes into the list as well, or the thread being read is the one
            # row the list does not have and nothing on screen looks selected.
            return [entry] + rows, entry

        def copy(text):
            """The answer as Codex wrote it, not the HTML rich_text() made of it."""
            ui.clipboard.write(text)
            ui.notify('답변을 클립보드에 복사했습니다.')

        @ui.refreshable
        def thread():
            lines = list(store(directory).chat(account, state['room'])) if account else []
            if not lines:
                empty('질문을 입력하면 Codex가 답합니다. 답변은 한 번에 하나씩 오고, '
                      '분석이 돌고 있으면 그 뒤에 처리됩니다.')
            for role, text, at in lines:
                mine = role == 'user'
                # The question goes in as text, which nicegui escapes; only the answer
                # is rendered, and only through the subset rich_text() knows about.
                if mine:
                    ui.chat_message([text], name='나', sent=True,
                                    stamp=line_text(at, '')[:14])
                    continue
                # The answer is the thing anyone wants out of this page — into a reply,
                # a ticket, a memo — and selecting rendered HTML by hand takes the
                # bullets and the code block with it.
                with ui.element('div').classes('ma-said'):
                    ui.chat_message([rich_text(text)], name='Codex', sent=False,
                                    stamp=line_text(at, '')[:14], text_html=True)
                    ui.button(icon='content_copy',
                              on_click=lambda answer=text: copy(answer)) \
                        .props('flat dense round size=sm') \
                        .classes('ma-said__copy').tooltip('답변 복사')
            if busy['now']:
                # The dots are the whole message: the composer is already disabled, and
                # the page says out loud that an answer takes a while.
                with ui.chat_message(name='Codex', sent=False):
                    with ui.element('div').classes('ma-wait'):
                        ui.spinner(type='dots', size='sm')

        async def scroll():
            """Yield a tick first: the new bubble is not laid out when refresh() returns.

            A ui.timer here would be built into whichever slot the handler happens to be
            in, and thread.refresh() has just deleted that one.
            """
            await asyncio.sleep(0.05)
            area.scroll_to(percent=1.0, duration=0.12)

        async def ask():
            question = (box.value or '').strip()
            if busy['now'] or not question:
                return
            if not account:
                ui.notify('설정을 먼저 저장하세요.')
                return
            # The room is captured here, not read at the end: an answer takes tens of
            # seconds and the reader may well have moved to another thread by then. It
            # belongs to the room it was asked in either way.
            room = state['room']
            box.set_value('')
            store(directory).add_chat(account, 'user', question, room)
            # A 새 대화 has no name until its first question, which is the name.
            if room.startswith(ROOM_MARK):
                store(directory).name_room(room, room_title(question), only_if_unnamed=True)
            busy['now'] = True
            # One Codex process at a time (services.codex_slot): a second question would
            # sit in that queue for the whole of this run with nothing on screen saying so.
            box.disable()
            send.disable()
            thread.refresh()
            rooms.refresh()
            await scroll()
            history = [(role, text) for role, text, _ in
                       store(directory).chat(account, room)][:-1]
            try:
                reply = await nicerun.io_bound(helpers.chat_reply, question, history,
                                               chat_context(mail_of(room)), config)
            except Exception as exc:
                reply = f'답하지 못했습니다: {type(exc).__name__}: {exc}'
            store(directory).add_chat(account, 'codex', reply, room)
            busy['now'] = False
            box.enable()
            send.enable()
            rooms.refresh()
            if state['room'] != room:
                # Answered into a thread nobody is looking at; the list already says so.
                ui.notify('다른 대화의 답변이 도착했습니다.')
                return
            thread.refresh()
            await scroll()

        def wipe():
            store(directory).clear_chat(account, state['room'])
            thread.refresh()
            rooms.refresh()
            head.refresh()

        async def open_room(key):
            if key == state['room']:
                return
            state['room'] = key
            thread.refresh()
            rooms.refresh()
            head.refresh()
            await scroll()

        async def start_room():
            if not account:
                ui.notify('설정을 먼저 저장하세요.')
                return
            state['room'] = store(directory).new_room(account)
            state['query'] = ''
            thread.refresh()
            rooms.refresh()
            head.refresh()
            # Refresh first, then touch the composer: box lives outside the refreshables.
            box.run_method('focus')
            await scroll()

        def rename(key):
            def save():
                store(directory).name_room(key, room_title(field.value))
                naming.close()
                rooms.refresh()
                head.refresh()
            naming = ui.dialog()
            with naming, card('대화 이름 바꾸기', 'edit'):
                field = ui.input(value=current()[1]['title']) \
                    .props('outlined dense autofocus').style('width:100%')
                field.on('keydown.enter', save)
                with ui.element('div').classes('ma-foot'):
                    ui.space()
                    ui.button('취소', on_click=naming.close).props('flat dense no-caps')
                    ui.button('저장', on_click=save).props('unelevated dense no-caps')
            naming.open()

        def remove(key):
            def go():
                store(directory).drop_room(account, key)
                asking.close()
                state['room'] = ''
                thread.refresh()
                rooms.refresh()
                head.refresh()
                ui.notify('대화를 지웠습니다.')
            asking = ui.dialog()
            with asking, card('대화 삭제', 'delete_outline'):
                ui.label('이 대화와 주고받은 내용을 모두 지웁니다. 되돌릴 수 없습니다.') \
                    .classes('ma-lede')
                with ui.element('div').classes('ma-foot'):
                    ui.space()
                    ui.button('취소', on_click=asking.close).props('flat dense no-caps')
                    ui.button('삭제', on_click=go) \
                        .props('unelevated dense no-caps color=negative')
            asking.open()

        @ui.refreshable
        def rooms():
            """The thread list. Rebuilt on every write, which is cheap: one grouped query."""
            shaped = room_search(current()[0], state['query'])
            if not shaped:
                empty('찾는 대화가 없습니다.')
                return
            for entry in shaped:
                classes = 'ma-room' + (' is-open' if entry['open'] else '')
                with ui.element('div').classes(classes) \
                        .on('click', lambda key=entry['key']: open_room(key)):
                    with ui.element('div').classes('ma-room__top'):
                        ui.icon(ROOM_ICONS[entry['kind']]).classes('ma-room__icon')
                        ui.label(entry['title']).classes('ma-room__title')
                        ui.space()
                        ui.label(entry['when']).classes('ma-room__when')
                    if entry['preview']:
                        ui.label(entry['preview']).classes('ma-room__last')

        @ui.refreshable
        def head():
            """The open thread's own line: what it is, and what may be done to it."""
            entry = current()[1]
            mail = mail_of(state['room'])
            with ui.element('div').classes('ma-head').style('margin-bottom:6px'):
                ui.icon(ROOM_ICONS[entry['kind']]).style(f'color:{MUTED};font-size:17px')
                ui.label(entry['title']).classes('ma-head__title')
                ui.space()
                if mail is not None:
                    ui.link('메일에서 보기', href('/mail', token, id=state['room'])) \
                        .style(f'color:{BRAND};font-size:12px;text-decoration:none')
                if entry['kind'] == 'room':
                    ui.button(icon='edit', on_click=lambda: rename(state['room'])) \
                        .props('flat dense round').tooltip('이름 바꾸기')
                    ui.button(icon='delete_outline', on_click=lambda: remove(state['room'])) \
                        .props('flat dense round text-color=negative').tooltip('대화 삭제')
                ui.button(icon='delete_sweep', on_click=wipe) \
                    .props('flat dense round').tooltip('주고받은 내용만 지우기')

        def filter_rooms(value):
            state['query'] = value or ''
            rooms.refresh()

        with page_shell('/chat'):
            with ui.element('div').classes('ma-chatwrap'):
                with card(flush=True).classes('ma-rooms'):
                    with ui.element('div').classes('ma-rooms__head'):
                        ui.label('대화').classes('ma-head__title')
                        ui.space()
                        ui.button('새 대화', icon='add', on_click=start_room) \
                            .props('flat dense no-caps')
                    ui.input(placeholder='대화 검색') \
                        .props('outlined dense clearable') \
                        .classes('ma-rooms__find') \
                        .on_value_change(lambda event: filter_rooms(event.value))
                    with ui.element('div').classes('ma-rooms__list ma-scroll'):
                        rooms()
                with card().classes('ma-thread'):
                    head()
                    ui.label('메일 화면에서 넘어오면 그 메일 내용을 함께 봅니다. 주의: 메일 본문이 '
                             '로그인한 Codex 계정으로 전송되며, 대화는 이 PC에만 저장됩니다.') \
                        .classes('ma-lede').style('margin-bottom:12px')
                    area = ui.scroll_area().classes('ma-chat')
                    with area:
                        thread()
                    with ui.element('div').classes('ma-compose'):
                        box = ui.textarea(
                            placeholder='질문을 입력하세요 · Enter 전송, Shift+Enter 줄바꿈')
                        box.props('outlined autogrow dense') \
                            .style('flex:1 1 auto;min-width:0')
                        # .exact so Shift+Enter still writes a newline; .prevent so the
                        # newline it would have written does not land in the empty box.
                        box.on('keydown.enter.exact.prevent', ask)
                        send = ui.button('보내기', icon='send', on_click=ask) \
                            .props('unelevated no-caps').style('flex:none')
            # A thread opens where it was left off, which is at the end of it.
            ui.timer(0.15, lambda: area.scroll_to(percent=1.0), once=True)

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
            with grid(minimum=190, gap=12):
                for label, value, icon in (('수집 총계', data['total'], 'inbox'),
                                           ('분석 대기', data['waiting'], 'hourglass_empty'),
                                           ('처리 완료', data['handled'], 'task_alt')):
                    with ui.element('div').classes('ma-kpi'):
                        with ui.element('div').classes('ma-kpi__icon'):
                            ui.icon(icon)
                        with ui.element('div'):
                            ui.label(label).classes('ma-kpi__label')
                            ui.label(str(value)).classes('ma-kpi__value')
            with ui.element('div').style('margin-top:14px'):
                with card(f"일별 처리량 · 최근 {data['days']}일", 'show_chart'):
                    if data['trend']:
                        chart(trend_option(data['trend']), 240)
                    else:
                        empty('아직 기록이 없습니다.')
            with grid(minimum=280).style('margin-top:14px'):
                # The same two charts as the 대시보드's, off the same counts, so they
                # open the same filtered list — a bar that was a link on one page and
                # a picture on the other would be the page teaching two lessons.
                with card('메일 종류', 'label', note=BAR_NOTE):
                    bar_link(chart(bar_option([(name, data['categories'][name])
                                               for name in CATEGORIES]),
                                   24 * len(CATEGORIES) + 12, cap=460),
                             'category', CATEGORIES, token)
                with card('우선순위', 'flag', note=BAR_NOTE):
                    bar_link(chart(bar_option([(name, data['priorities'][name])
                                               for name, _ in PRIORITIES], STATUS),
                                   24 * len(PRIORITIES) + 12, cap=460),
                             'priority', PRIORITY_NAMES, token)

        def pick(days):
            span['days'] = days
            body.refresh()

        with page_shell('/stats'):
            with ui.element('div').style('display:flex;margin-bottom:14px'):
                ui.toggle({days: f'{days}일' for days in (7, 30, 90)}, value=span['days'],
                          on_change=lambda event: pick(event.value)) \
                    .props('no-caps dense unelevated toggle-color=primary').classes('ma-seg')
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
            tone, _ = lamp(view['running'], view['stopping'])
            with grid(minimum=200, gap=12):
                lamps = (('실행', view['label'], tone, 'play_circle'),
                         ('다음 확인', view['countdown'],
                          BRAND if view['running'] else MUTED, 'schedule'),
                         ('메일 비밀번호', view['password'],
                          OK if view['password'] == '저장됨' else css_color(SOON), 'key'))
                for label, value, colour, icon in lamps:
                    with ui.element('div').classes('ma-kpi'):
                        with ui.element('div').classes('ma-kpi__icon') \
                                .style(f'background:{soft_of(colour)};color:{colour}'):
                            ui.icon(icon)
                        with ui.element('div').style('min-width:0'):
                            ui.label(label).classes('ma-kpi__label')
                            ui.label(value).style(f'color:{INK};font-size:14.5px;'
                                                  'font-weight:600;line-height:1.5')
                for label, value in view['stamps'].items():
                    with ui.element('div').classes('ma-kpi'):
                        with ui.element('div').classes('ma-kpi__icon'):
                            ui.icon('history')
                        with ui.element('div').style('min-width:0'):
                            ui.label(label).classes('ma-kpi__label')
                            ui.label(value).style(f'color:{INK};font-size:14.5px;'
                                                  'font-weight:600;line-height:1.5')
            if not can_start:
                with ui.element('div').classes('ma-alert ma-alert--warn') \
                        .style('margin-top:14px'):
                    ui.icon('info_outline').style('font-size:16px')
                    ui.label('창이 이미 실행 중입니다. 이 화면에서는 수집을 시작할 수 없습니다 — '
                             '수집기는 하나만 돕니다.').style('font-size:12px')
            for blocker in view['blockers']:
                with ui.element('div').classes('ma-alert').style('margin-top:10px'):
                    ui.icon('error_outline').style('font-size:16px')
                    ui.label('설정 확인: ' + blocker).style('font-size:12px')
            with ui.element('div').style('margin-top:14px'):
                with card('기록', 'receipt_long'):
                    if view['message']:
                        ui.label(view['message']).style(f'color:{INK};font-size:13px;'
                                                        'margin-bottom:8px')
                    if not view['lines']:
                        empty('아직 기록이 없습니다.')
                    else:
                        with ui.element('div').classes('ma-sunken ma-scroll ma-log') \
                                .style('display:block;max-height:380px;width:100%'):
                            for text in view['lines']:
                                ui.label(text).style('white-space:pre-wrap;'
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

        with page_shell('/run'):
            with card().style('padding:12px 14px;margin-bottom:14px'):
                with ui.element('div').style('display:flex;gap:6px;flex-wrap:wrap;'
                                             'align-items:center'):
                    ui.button('시작', icon='play_arrow', on_click=begin) \
                        .props('unelevated dense no-caps')
                    ui.button('중지', icon='stop', on_click=stop) \
                        .props('outline dense no-caps')
                    ui.button('지금 확인', icon='refresh', on_click=now) \
                        .props('flat dense no-caps text-color=secondary')
                    ui.button('연결 테스트', icon='lan', on_click=test) \
                        .props('flat dense no-caps text-color=secondary')
                    ui.button('Codex 로그인', icon='terminal', on_click=codex_login) \
                        .props('flat dense no-caps text-color=secondary')
                    ui.space()
                    ui.button('엑셀 열기', icon='table_view',
                              on_click=lambda: reveal(config.get('workbook', ''), '엑셀 파일')) \
                        .props('flat dense no-caps text-color=secondary')
                    ui.button('데이터 폴더', icon='folder_open',
                              on_click=lambda: reveal(directory, '데이터 폴더')) \
                        .props('flat dense no-caps text-color=secondary')
            body()
            with grid(minimum=300).style('margin-top:14px'):
                with card('Codex 사용량', 'speed'):
                    usage_panel()
                with card('업데이트', 'system_update_alt'):
                    update_panel()
            ui.timer(LIVE_SECONDS, body.refresh)

    # 설정 -------------------------------------------------------------

    @ui.page('/settings')
    def settings_page(request: Request):
        if not allowed(request):
            refused()
            return
        values = {key: str(config.get(key, '')) for key, _ in FIELDS if key != 'password'}
        inputs, notes = {}, {}
        # 진단 used to be a page of its own. It is two buttons and a result list that
        # nobody opens until something is already wrong, and everything it asks about —
        # the account, the password, the Codex login — is set on this page.
        found = {'steps': [], 'codex': None}
        # Codex's own list, read fresh: the slugs an account may use turn over, and a
        # radio button naming a retired one fails every analysis.
        models = read_models()
        rows = model_rows(models, values['model'])

        def validate():
            errors = field_errors(values)
            for key, note in notes.items():
                message = errors.get(key, '')
                note.set_text(message)
                note.style(f"color:{css_color(URGENT)};font-size:11px"
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

        @ui.refreshable
        def report_block():
            if not found['steps'] and not found['codex']:
                empty('위 버튼으로 점검을 실행하세요.')
                return
            for label, ok, detail in found['steps']:
                with ui.element('div').classes('ma-row'):
                    ui.icon('check_circle' if ok else 'cancel') \
                        .style(f"color:{OK if ok else css_color(URGENT)};font-size:17px")
                    ui.label(label).style(f'color:{INK};font-size:13px;font-weight:600;'
                                          'min-width:118px')
                    ui.label(detail).classes('ma-meta__item').style('overflow-wrap:anywhere')
            if found['codex']:
                state, detail = found['codex']
                with ui.element('div').classes('ma-row'):
                    ui.icon('terminal').style(f'color:{MUTED};font-size:17px')
                    ui.label('Codex').style(f'color:{INK};font-size:13px;font-weight:600;'
                                            'min-width:118px')
                    ui.label(f'{state} — {detail}').classes('ma-meta__item') \
                        .style('overflow-wrap:anywhere')

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

        def choose(value):
            """The dropdown is the whole answer now: whatever it holds is a Codex slug."""
            values['model'] = str(value or '')
            model_note.refresh()
            validate()

        with page_shell('/settings'):
            with ui.element('div').style('max-width:660px;display:grid;gap:14px'):
                with card('메일과 엑셀', 'tune'):
                    for key, label in FIELDS:
                        if key in ('password', 'model'):
                            continue
                        box = ui.input(label=label, value=values[key]).classes('w-full')
                        box.props('dense outlined stack-label').style('margin-bottom:2px')
                        box.on_value_change(
                            lambda event, name=key: (values.update({name: event.value or ''}),
                                                     validate()))
                        inputs[key] = box
                        notes[key] = ui.label('').classes('ma-meta__item') \
                            .style('margin:0 0 8px 2px')
                    ui.button('저장', icon='save', on_click=save) \
                        .props('unelevated dense no-caps').style('margin-top:4px')

                with card('Codex 모델', 'smart_toy'):
                    ui.label('메일 분석과 상담에 쓸 모델입니다. 목록은 Codex CLI가 이 PC에 '
                             '저장해 둔, 지금 계정으로 쓸 수 있는 모델입니다.') \
                        .classes('ma-lede').style('margin-bottom:10px')
                    if not models:
                        with ui.element('div').classes('ma-alert').style('margin-bottom:10px'):
                            ui.icon('info_outline').style('font-size:16px')
                            ui.label(NO_MODELS).style('font-size:12px')
                    ui.select({row['value']: model_option(row) for row in rows},
                              value=values['model'],
                              on_change=lambda event: choose(event.value)) \
                        .props('dense outlined options-dense') \
                        .classes('w-full ma-select')

                    @ui.refreshable
                    def model_note():
                        """The row the dropdown is holding, written out under it.

                        The chips are the same two words the closed dropdown shows, and
                        only 사용량 carries a colour: that is the half somebody is
                        choosing for, and accenting both would accent neither — the same
                        arithmetic 분석 결과 spends its accent by.
                        """
                        row = next((item for item in rows
                                    if item['value'] == values['model']), rows[0])
                        cost = COST_TONES.get(row['cost'], SUBTLE)
                        with ui.element('div').classes('ma-sunken') \
                                .style('margin-top:10px;padding:10px 12px'):
                            with ui.element('div').style('display:flex;gap:6px;'
                                                         'align-items:center;flex-wrap:wrap'):
                                ui.label(row['name']).style(f'color:{INK};font-size:13px;'
                                                            'font-weight:700')
                                tag(row['power'], SUBTLE)
                                tag(row['cost'], cost, soft_of(cost))
                                if row.get('recommended'):
                                    tag('추천', BRAND, BRAND_SOFT)
                            if row['hint']:
                                ui.label(row['hint']).classes('ma-meta__item') \
                                    .style('margin:6px 0 0;overflow-wrap:anywhere')
                        # 왜 그것이 추천인지는 고른 줄 아래에 한 번만. 목록 안에서는
                        # 칩 하나로 충분하고, 여섯 줄에 같은 문장을 여섯 번 쓸 수는 없다.
                        if row.get('recommended'):
                            ui.label(RECOMMEND_WHY).classes('ma-meta__item') \
                                .style('margin:6px 0 0 2px')
                        if row['power'] or row['cost']:
                            ui.label(GRADE_NOTE).classes('ma-meta__item') \
                                .style('margin:6px 0 0 2px')

                    model_note()
                    notes['model'] = ui.label('').classes('ma-meta__item') \
                        .style('margin:2px 0 0 2px')
                    ui.button('저장', icon='save', on_click=save) \
                        .props('unelevated dense no-caps').style('margin-top:10px')

                with card('메일 전용 비밀번호', 'key'):
                    ui.label('Windows 자격 증명에 저장됩니다. 설정 파일에는 기록되지 않습니다.') \
                        .classes('ma-lede').style('margin-bottom:10px')
                    password_box = ui.input(password=True, placeholder='입력 후 저장') \
                        .classes('w-full')
                    password_box.props('dense outlined')
                    with ui.element('div').style('display:flex;gap:6px;margin-top:10px'):
                        ui.button('비밀번호 저장', icon='lock', on_click=save_password) \
                            .props('unelevated dense no-caps')
                        ui.button('저장된 비밀번호 삭제', icon='delete_outline',
                                  on_click=drop_password).props('outline dense no-caps')

                with card('점검', 'troubleshoot'):
                    ui.label('연결 실패와 분석 실패의 원인을 단계별로 확인합니다. '
                             '아무것도 수정하지 않고 읽기만 합니다.') \
                        .classes('ma-lede').style('margin-bottom:10px')
                    with ui.element('div').style('display:flex;gap:6px;flex-wrap:wrap;'
                                                 'margin-bottom:4px'):
                        ui.button('메일 연결 점검', icon='lan', on_click=check_mail) \
                            .props('unelevated dense no-caps')
                        ui.button('Codex 상태 점검', icon='terminal', on_click=check_codex) \
                            .props('outline dense no-caps')
                    report_block()

                with card('업데이트', 'system_update_alt'):
                    update_panel()

                with card('이 컴퓨터', 'computer'):
                    for label, value in (('버전', __version__), ('데이터 폴더', str(directory)),
                                         ('설정 파일', str(config_path)),
                                         ('데이터베이스', str(directory / 'mail.db'))):
                        with ui.element('div').classes('ma-row'):
                            ui.label(label).classes('ma-meta__item').style('min-width:92px')
                            ui.label(value).style(f'color:{SUBTLE};font-size:12px;'
                                                  'overflow-wrap:anywhere')
                    ui.label('메일 본문은 분석을 위해 로그인한 Codex 계정으로 전송됩니다.') \
                        .classes('ma-lede').style('margin:10px 0 0')
            validate()


def serve(directory, config, token=None, host='127.0.0.1', port=None, native=False, show=False,
          hub=None, services=None, config_path=None, can_start=True, updater=None):
    """Loopback only: the page serves mail content and must not be reachable off the PC."""
    from nicegui import app, ui
    token = token or new_token()
    port = port or open_port(host)
    build(directory, config, token, hub, services, config_path, can_start, updater)
    app.on_shutdown(release_store)
    # Whatever opens the page has to carry the token, or it lands on the refusal.
    # nicegui's `show` takes a path (it appends it to the root URL), and the native
    # window's URL comes from window_args, which it merges over its own.
    target = f'/?t={token}' if token else '/'
    if native:
        width, height = window_size(screen_size())
        app.native.window_args['url'] = f'http://{host}:{port}{target}'
        # min_size is clamped too: on a small screen a minimum larger than the window
        # is what pywebview obeys, and the frame opens off the bottom of the desktop.
        app.native.window_args.update(width=width, height=height,
                                      min_size=(min(WINDOW_MIN[0], width),
                                                min(WINDOW_MIN[1], height)))
    # flush: a redirected stdout is block-buffered, and spike.ps1 reads this line
    # out of the log to know the server is up.
    print(f'메일 도우미 대시보드: http://{host}:{port}{target}', flush=True)
    ui.run(host=host, port=port, reload=False, show=target if show else False,
           native=native, dark=False, title=f'메일 도우미 {__version__}',
           storage_secret=token, favicon='📬')
