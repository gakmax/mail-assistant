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
from .calendar_sheet import COLORS, MARKERS
from .core import (ANALYZING, FAILED, HANDLED, LIST_LIMIT, PROGRESS, ROOM_MARK, SORTS, STATES,
                   Store, account_key, local_text, now, parse_mail, row_view, state_of)
from .dashboard import CATEGORIES, PRIORITIES, describe
from .excel import mailto
from .hub import line_text
from .overview import (DUE_DAYS, RECENT_DAYS, UPCOMING, overview, past_due, results,
                       review_queue, trend)
from .settings import (CUSTOM, DEFAULTS, FIELDS, NO_MODELS, field_errors, model_rows,
                       model_value, normalize, read_models)
from .style import CALM, DASH_GREEN, LINK, NEUTRAL, SOON, URGENT, css_color

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
              '검토 전 초안': SERIES, FAILED_CARD: css_color(URGENT)}
REFRESH_SECONDS = 5.0
# 실행 and 메일 read their own numbers every second: both are screens somebody opens
# *because* something is moving, and five seconds of a still picture reads as a hang.
# 대시보드 keeps the slower beat — its charts and tallies are a day's shape, not a
# pulse — but repaints at once when another screen says it changed (Hub.touch).
LIVE_SECONDS = 1.0
RUN_LINES = 8
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
         ('/chat', '상담', 'forum'), ('/stats', '통계', 'insights'),
         ('/run', '실행', 'play_circle'), ('/settings', '설정', 'settings'))
# The sidebar's groups, in the order they are drawn. Nine destinations in one flat
# row read as nine; grouped they read as four questions. 대시보드 keeps no heading —
# it is where the window opens, and a label over a single item is noise. Only paths
# live here, so PAGES stays the one list of destinations; a test holds the two equal,
# because a page added there and forgotten here would be reachable by URL and by
# nothing else on screen.
NAV_GROUPS = (('', ('/',)),
              ('업무', ('/mail', '/calendar', '/todo', '/drafts')),
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
# Past this a badge is a shape rather than a number, and the sidebar has one width.
NAV_BADGE_MAX = 99
# Monday first, and never through strftime: Windows encodes a format string with the
# locale codec, so a Korean pattern raises UnicodeEncodeError off a Korean PC.
WEEKDAYS = ('월', '화', '수', '목', '금', '토', '일')
# The 대시보드 cards. '…일 내 마감' carries the window in its name, so it is matched by suffix.
CARD_ICONS = {'미처리 메일': 'inbox', '긴급·높음': 'priority_high', '검토 전 초안': 'edit_note',
              FAILED_CARD: 'error_outline'}
# The 메일 list's columns, in the order the table draws them. Every key is also a key
# of core.SORTS, which is what lets the header itself be the sort control — a test
# holds the two together, because a header with no SQL behind it would sort nothing.
LIST_FIELDS = (('received', '수신'), ('sender', '발신자'), ('subject', '제목'),
               ('category', '종류'), ('priority', '우선순위'), ('state', '상태'))
# Columns a first click should sort from ㄱ. The rest read newest or most urgent first.
TEXT_SORTS = ('subject', 'sender', 'category')
DEFAULT_LIST = {'query': '', 'state': '', 'sort': 'received', 'desc': True,
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
}}
.ma-side__brand {{
  display:flex; align-items:center; gap:9px; padding:4px 8px 8px;
  text-decoration:none; color:inherit;
}}
.ma-side__mark {{
  display:grid; place-items:center; width:28px; height:28px; border-radius:8px;
  background:var(--brand); color:#fff; flex:none;
}}
.ma-side__mark svg {{ width:16px; height:16px; display:block; }}
.ma-side__words {{ display:flex; flex-direction:column; min-width:0; }}
.ma-side__name {{
  font-size:13.5px; font-weight:700; letter-spacing:-.01em; white-space:nowrap;
}}
.ma-side__ver {{ font-size:10.5px; color:var(--muted); }}
.ma-side__group {{
  font-size:10.5px; font-weight:700; letter-spacing:.06em;
  color:var(--muted); padding:13px 10px 5px;
}}
.ma-side__item {{
  position:relative; display:flex; align-items:center; gap:9px;
  padding:7px 10px; border-radius:9px; text-decoration:none;
  color:var(--subtle); font-size:13px; font-weight:500;
  transition:background .12s ease, color .12s ease;
}}
.ma-side__item:hover {{ background:var(--sunken); color:var(--ink); }}
.ma-side__item.is-live {{
  background:var(--brand-soft); color:var(--brand); font-weight:600;
}}
.ma-side__item .q-icon {{ font-size:18px; flex:none; }}
.ma-side__label {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.ma-badge {{
  margin-left:auto; flex:none; min-width:19px; height:19px; padding:0 6px;
  border-radius:999px; background:var(--sunken); border:1px solid var(--line);
  color:var(--muted); font-size:10.5px; font-weight:700; line-height:17px;
  text-align:center;
}}
.ma-side__item.is-live .ma-badge {{
  background:var(--brand); border-color:var(--brand); color:#fff;
}}
@media (max-width:{SIDE_BREAK - 1}px) {{
  .ma-side {{ width:{SIDE_RAIL}px; padding:14px 8px 18px; }}
  .ma-side__words, .ma-side__label, .ma-side__group {{ display:none; }}
  .ma-side__brand {{ justify-content:center; padding:4px 0 10px; }}
  .ma-side__item {{ justify-content:center; padding:8px 0; }}
  /* No room for the count, so the badge becomes the fact that there is one. */
  .ma-badge {{
    position:absolute; top:5px; right:11px; margin:0; padding:0;
    width:8px; height:8px; min-width:0; font-size:0; line-height:0;
    border-radius:50%; background:var(--brand); border-color:var(--card);
  }}
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
.ma-alert {{
  display:flex; gap:7px; align-items:flex-start; border-radius:9px; padding:9px 11px;
  color:var(--urgent); background:rgba(192,0,0,.06); border:1px solid rgba(192,0,0,.14);
}}
.ma-alert--warn {{
  color:var(--soon); background:rgba(208,112,0,.07); border-color:rgba(208,112,0,.16);
}}
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

/* 상담: the two speakers differ by side and by ground, never by side alone.
   Quasar's q-chat-message brings its own palette and a little tail; the rules below
   are scoped to .ma-chat so nothing else on the site inherits the override. */
.ma-chat {{ height:min(58vh, 470px); width:100%; }}
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

/* 상담: the thread list beside the thread. One column on a narrow window, where a
   250px sidebar would leave the bubbles no room at all. */
.ma-chatwrap {{ display:flex; gap:14px; align-items:stretch; width:100%; }}
.ma-rooms {{ flex:0 0 252px; min-width:0; display:flex; flex-direction:column; padding:0; }}
.ma-thread {{ flex:1 1 auto; min-width:0; }}
@media (max-width:900px) {{
  .ma-chatwrap {{ flex-direction:column; }}
  .ma-rooms {{ flex:none; width:100%; }}
  .ma-rooms__list {{ max-height:220px; }}
}}
.ma-rooms__head {{
  display:flex; align-items:center; gap:8px; padding:12px 12px 6px;
}}
.ma-rooms__find {{ padding:0 12px 8px; width:100%; }}
.ma-rooms__list {{ flex:1 1 auto; max-height:min(58vh, 470px); padding:0 6px 8px; }}
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
    """Horizontal count bars. showBackground draws the empty track a 0 could not."""
    names = [name for name, _ in pairs]
    data = [{'value': value,
             'itemStyle': {'color': (tones or {}).get(name, SERIES), 'borderRadius': 4}}
            for name, value in pairs]
    option = dict(chart_base(left=0, right=34, top=6, bottom=0))
    option.update({
        'xAxis': dict(axis_style(), type='value', show=False),
        # ECharts draws a category axis bottom-up, so reverse to read top-down.
        'yAxis': dict(axis_style(INK), type='category', data=list(reversed(names)),
                      axisLabel={'color': INK, 'fontSize': 12}),
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


def plain_title(label):
    """An entry label without the marker calendar_sheet puts on the front of it.

    ■/▶/◆ are how an Excel cell says 마감·시작·확인 필요 in one colour of text. Every
    screen that shows an entry draws KIND_ICONS beside it instead, in the kind's own
    colour, so the marker would be the same thing said twice — and it arrived here in
    the surrounding text's colour, which said nothing at all.
    """
    text = str(label or '')
    if text[:1] in MARKERS.values():
        text = text[1:].lstrip()
    return text


def today_rows(events, today, handled=()):
    """오늘 날짜에 걸린 일정 — 시작도 마감도, 처리 완료된 메일만 빼고.

    The 마감 panel beside it answers '언제까지인가'; this one answers '오늘 무엇이
    있는가', which is why 시작 is kept here and dropped there.
    """
    return [{'kind': entry.kind, 'title': plain_title(entry.label), 'mail': entry.mail_id,
             'evidence': entry.evidence}
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


def clicked_key(args):
    """The column name a header click carried.

    A slot template reaches the server through $parent.$emit, and Element.on hands the
    handler the client's argument *list* — ['received'], not 'received'. A bare string
    is taken too, so the caller never has to know which shape arrived.
    """
    if isinstance(args, (list, tuple)):
        return str(args[0]) if args else ''
    return str(args or '')


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


def list_signature(data):
    """What a repaint of the 메일 목록 would actually change.

    The list polls every second and a rebuild is also what clears q-table's
    checkboxes, so it has to be able to tell 'the worker analysed something' from
    'nothing happened'. Every column the table draws is in here; `received` is not,
    because it is fixed once the mail is stored.
    """
    return (data['total'], data['page'],
            tuple((row['id'], row['state'], row['category'], row['priority'],
                   row['subject'], row['sender']) for row in data['rows']))


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
        'todo_hidden': bool(row['todo_hidden']),
    }


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


def card(title=None, icon=None, flush=False):
    """The one surface everything sits on. Returns the element, so callers can `with` it."""
    from nicegui import ui
    box = ui.element('div').classes('ma-card' + (' ma-card--flush' if flush else ''))
    if title:
        with box, ui.element('div').classes('ma-head'):
            if icon:
                ui.icon(icon).style(f'color:{MUTED};font-size:17px')
            ui.label(title).classes('ma-head__title')
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
    if name == FAILED_CARD:
        return href('/mail', token, state=FAILED)
    return None


def card_hint(name, data):
    """The line under a card's number. A total says how many, never how long."""
    if name == '미처리 메일' and data.get('oldest'):
        return f"가장 오래된 건 {data['oldest']}일 경과"
    if name == FAILED_CARD:
        # worker.py backs off 5·10·20·40 minutes and then stays hourly, for ever: there
        # is no attempt cap, so the honest line is how often, not whether.
        return '최대 1시간 간격으로 자동 재시도'
    return ''


def card_rows(data):
    """The four fixed cards, plus 분석 실패 only while there is something to report."""
    rows = list(data['cards'].items())
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
                ui.link(row['title'], href('/mail', token, id=row['mail'])) \
                    .classes('ma-due__title')
                ui.space()
                ui.label(row['left']).classes(
                    'ma-due__left' + (' is-missed' if row['missed'] else ''))


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
                    ui.link(row['title'], href('/mail', token, id=row['mail'])) \
                        .classes('ma-today__title')
                    ui.space()
                    ui.label(row['kind']).classes('ma-today__kind')


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


def lamp(running, stopping=False):
    """(colour, label) for the worker's state, shared by the strip and the 실행 page."""
    if stopping:
        return css_color(SOON), '중지 중'
    return (OK, '실행 중') if running else (MUTED, '중지됨')


def run_strip(summary, token=None):
    from nicegui import ui
    tone, _ = lamp(summary['running'], summary['stopping'])
    with card():
        with ui.element('div').classes('ma-head').style('margin-bottom:6px'):
            ui.element('div').classes('ma-dot').style(f'background:{tone}')
            ui.label(summary['label']).classes('ma-head__title')
            ui.space()
            if token:
                ui.link('실행 화면 열기', href('/run', token)) \
                    .style(f'color:{BRAND};font-size:12px;text-decoration:none')
        with ui.element('div').classes('ma-meta').style('margin-bottom:10px'):
            for label, value in summary['stamps'].items():
                ui.label(f'{label} {value}').classes('ma-meta__item')
        if summary['message']:
            ui.label(summary['message']).style(f'color:{INK};font-size:12.5px;margin-bottom:8px')
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


def update_pill(offer):
    """'새 버전 0.5.1', or ''. The header only ever says there is one; 실행 says what
    it costs and is where the button lives."""
    version = (offer or {}).get('version') if isinstance(offer, dict) else None
    return f'새 버전 {version}' if version else ''


def shell(current, token, chrome=None):
    """The sidebar, the header band and the page area, identical on every page.

    `chrome` is one callable returning {'counts', 'state', 'offer'} — one call per tick
    for all three, because the badges, the status chip and the update pill move on the
    same beat and re-reading the mailbox once per element is what it is there to avoid.
    It is passed in rather than taken from a hub, so this module keeps importing without
    nicegui and nav_rows()/bar_status() stay coverable on Linux.
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
        latest = chrome() if chrome is not None else {}
        marks = {}

        def paint(state, line):
            """Everything about the chip and the pill that is not their text. Bound
            late on purpose: the elements below do not exist yet when this is read."""
            dot.style(f"background:{state['tone']}")
            if state['beat']:
                chip.classes(add='is-live')
            else:
                chip.classes(remove='is-live')
            chip.set_visibility(bool(state['text']))
            pill.set_visibility(bool(line))

        with ui.element('div').classes('ma-shell'):
            with ui.element('aside').classes('ma-side'):
                with ui.link(target=href('/', token)).classes('ma-side__brand'):
                    with ui.element('div').classes('ma-side__mark'):
                        ui.html(BRAND_MARK)
                    with ui.element('div').classes('ma-side__words'):
                        ui.label('메일 업무 도우미').classes('ma-side__name')
                        ui.label(__version__).classes('ma-side__ver')
                with ui.element('nav'):
                    for heading, items in nav_rows(current, latest.get('counts')):
                        if heading:
                            ui.label(heading).classes('ma-side__group')
                        for path, name, icon, live, badge in items:
                            classes = 'ma-side__item' + (' is-live' if live else '')
                            with ui.link(target=href(path, token)).classes(classes):
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
                        paint(shown, line)
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
            if state['text'] != word.text or found != offer.text:
                word.set_text(state['text'])
                offer.set_text(found)
                paint(state, found)

        if chrome is not None:
            # Built while the page is, never inside a handler that has refreshed:
            # a timer takes its client from the current slot.
            ui.timer(REFRESH_SECONDS, tick)

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
      body.push('<div class="ma-tip__hint">누르면 이 일정이 나온 메일이 열립니다.</div>');
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

    # 사이드바 ---------------------------------------------------------

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
                'offer': updater.offer if updater is not None else None}

    def page_shell(current):
        """The shell every page opens with, wired to this build's hub and updater."""
        return shell(current, token, chrome)

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

    # 대시보드 ---------------------------------------------------------

    @ui.page('/')
    def home(request: Request):
        if not allowed(request):
            refused()
            return

        window = {'days': DUE_DAYS}
        latest = {}

        def read():
            """One read per tick: every block and chart below sees the same rows.

            Inlined rather than snapshot(): the kanban tally needs the same rows and a
            second page() every five seconds would read the whole mailbox twice.
            """
            account = account_of(config)
            rows = list(store(directory).page(account)) if account else []
            todos = list(store(directory).todos(account)) if account else []
            today = date.today()
            latest['data'] = overview(rows, today, within=window['days'])
            latest['todo'] = board_counts(rows, todos)
            latest['today'] = today_rows(latest['data']['events'], today,
                                         latest['data']['handled'])
            latest['trend'] = trend(store(directory), account, today)
            note_counts(rows, todos)
            return latest['data']

        @ui.refreshable
        def kpi_row():
            cards(latest['data'], token)

        @ui.refreshable
        def today_block():
            today_panel(latest['today'], date.today(), token)

        @ui.refreshable
        def deadline_block():
            deadlines(latest['data'], date.today(), token, tick)

        @ui.refreshable
        def todo_block():
            todo_tally(latest['todo'], token)

        @ui.refreshable
        def run_block():
            if hub is not None:
                run_strip(run_summary(hub, directory, config), token)

        @ui.refreshable
        def summary_row():
            ui.label(summary_line(latest['data'])).classes('ma-meta__item')

        def tick(ident, done):
            """One mail can carry several deadlines; all its rows move together.

            The tick writes the mail's own 처리 상태 — the same field the kanban moves —
            so the two screens cannot disagree about what is finished.
            """
            store(directory).set_handled(ident, HANDLED if done else '')
            bump()
            read()
            # The kanban's middle column is this same field, so its tally moves too,
            # and a handled mail leaves 오늘 일정 the way it leaves the calendar.
            for block in (kpi_row, today_block, deadline_block, todo_block, summary_row):
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
            kpi_row()
            with ui.element('div').classes('ma-split').style('margin-top:14px'):
                with ui.element('div').classes('ma-stack'):
                    with card('일별 처리량', 'show_chart'):
                        daily = chart(trend_option(latest['trend']), 196)
                    with grid(minimum=240):
                        with card('메일 종류', 'label'):
                            kinds = chart(bar_option(
                                [(name, latest['data']['categories'][name])
                                 for name in CATEGORIES]),
                                24 * len(CATEGORIES) + 12, cap=460)
                        with card('우선순위', 'flag'):
                            ranks = chart(bar_option(
                                [(name, latest['data']['priorities'][name])
                                 for name, _ in PRIORITIES], STATUS),
                                24 * len(PRIORITIES) + 12, cap=460)
                with ui.element('div').classes('ma-stack'):
                    today_block()
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
                    run_block()
            with ui.element('div').style('margin-top:14px'):
                summary_row()

            def paint():
                data = read()
                for block in (kpi_row, today_block, deadline_block, todo_block, run_block,
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
        # A card link arrives with its own filter; it wins over what was remembered.
        for key in ('state', 'sort', 'query'):
            if key in request.query_params:
                saved[key] = request.query_params[key]
                saved['page'] = 0
        state = list_state(saved)
        opened = request.query_params.get('id')
        if opened:
            state['selected'] = opened
        wanted = {'ids': []}        # what the 삭제 confirmation is standing over
        touched = {'now': False}    # did the open mail change anything the list shows
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
                          '상담 기록도 함께 사라지며 되돌릴 수 없습니다. 이미 엑셀에 반영된 행은 '
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

            async def copy():
                await ui.clipboard.write(draft.value or '')
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
                with grid(minimum=320, gap=22).style('margin-top:16px;align-items:start'):
                    with ui.element('div'):
                        section('분석 결과', top=False)
                        if not view['analysed']:
                            empty('아직 분석되지 않았습니다.')
                        for label, text in (('요약', view['summary']),
                                            ('요청사항', view['requests']),
                                            ('우선순위 근거', view['reason']),
                                            ('다음 행동', view['action'])):
                            if text:
                                ui.label(label).classes('ma-field')
                                ui.label(text).style(f'color:{INK};font-size:13px;'
                                                     'white-space:pre-wrap;line-height:1.65')
                        if view['events']:
                            ui.label('일정').classes('ma-field')
                            for event in view['events']:
                                note = ' · 확인 필요' if event.get('needs_review') else ''
                                with ui.element('div').classes('ma-sunken') \
                                        .style('padding:9px 11px;margin-bottom:6px'):
                                    ui.label(f"{event.get('title', '')}{note}") \
                                        .style(f'color:{INK};font-size:12.5px;font-weight:600')
                                    ui.label(f"시작 {event.get('start') or '—'}"
                                             f" · 마감 {event.get('deadline') or '—'}") \
                                        .classes('ma-meta__item')
                                    if event.get('evidence'):
                                        ui.label(f"근거: {event['evidence']}") \
                                            .classes('ma-meta__item') \
                                            .style('white-space:pre-wrap')
                        if view['attachments']:
                            ui.label('첨부').classes('ma-field')
                            with ui.element('div').classes('ma-meta'):
                                for name in view['attachments']:
                                    with ui.element('div').classes('ma-tag') \
                                            .style(tag_style(SUBTLE) + ';gap:3px'):
                                        ui.icon('attach_file').style('font-size:13px')
                                        ui.label(name)
                    with ui.element('div'):
                        section('원문', top=False)
                        # display:block on purpose: a bare div inherits a centring flex
                        # layout here, which parks a short body in the middle of the box.
                        with ui.element('div').classes('ma-sunken ma-scroll') \
                                .style('display:block;max-height:340px;width:100%'):
                            ui.label(view['body'] or '(본문 없음)') \
                                .style(f'display:block;color:{SUBTLE};font-size:12.5px;'
                                       'white-space:pre-wrap;text-align:left;line-height:1.75;'
                                       'overflow-wrap:anywhere')
                with ui.element('div').classes('ma-head').style('margin:18px 0 6px'):
                    ui.label('답변 초안').classes('ma-head__title')
                    ui.label('입력을 멈추면 자동 저장됩니다.').classes('ma-meta__item')
                    ui.space()
                    ui.button('초안 복사', icon='content_copy', on_click=copy) \
                        .props('flat dense no-caps')
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
                    ui.label('일정을 누르면 그 메일이 열립니다.').classes('ma-meta__item')
                ui.element('div').props('id=calendar').style('width:100%')
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
                    empty('검토할 초안이 없습니다. 답변이 필요한 메일이 분석되면 여기에 모입니다.')
                return
            view = data['current']

            def step(delta):
                ids = [item['id'] for item in data['queue']]
                chosen['id'] = ids[(data['index'] + delta) % len(ids)]
                queue.refresh()

            async def copy():
                await ui.clipboard.write(draft.value or '')
                ui.notify('초안을 복사했습니다.')

            def done():
                store(directory).set_handled(view['id'], HANDLED)
                bump()
                chosen['id'] = None
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
                    ui.label('생성된 초안').classes('ma-head__title')
                    ui.label('입력을 멈추면 자동 저장됩니다.').classes('ma-meta__item')
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
            ui.label('답변이 필요한데 아직 손대지 않은 초안입니다. 입력을 멈추면 자동 저장되고, '
                     '저장하면 검토 완료로 간주해 목록에서 빠집니다.').classes('ma-lede')
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
            # A key nobody has written to yet is not in the list: shape it on its own.
            return rows, room_rows([{'key': state['room'], 'at': '', 'turns': 0,
                                     'name': ''}], state['room'])[0]

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
                ui.chat_message([text if mine else rich_text(text)],
                                name='나' if mine else 'Codex', sent=mine,
                                stamp=line_text(at, '')[:14], text_html=not mine)
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
                with card('메일 종류', 'label'):
                    chart(bar_option([(name, data['categories'][name])
                                      for name in CATEGORIES]),
                          24 * len(CATEGORIES) + 12, cap=460)
                with card('우선순위', 'flag'):
                    chart(bar_option([(name, data['priorities'][name])
                                      for name, _ in PRIORITIES], STATUS),
                          24 * len(PRIORITIES) + 12, cap=460)

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
            with ui.element('div').style('margin-top:14px'):
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
        picked = {'choice': values['model'], 'typed': ''}

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
            picked['choice'] = value
            values['model'] = model_value(value, picked['typed'])
            model_note.refresh()
            validate()

        def typed(text):
            picked['typed'] = text or ''
            values['model'] = model_value(picked['choice'], picked['typed'])
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
                    ui.radio({value: label for value, label, _ in rows},
                             value=picked['choice'],
                             on_change=lambda event: choose(event.value)) \
                        .props('dense').classes('ma-radio')

                    @ui.refreshable
                    def model_note():
                        hint = next((text for value, _, text in rows
                                     if value == picked['choice']), '')
                        if hint:
                            ui.label(hint).classes('ma-meta__item').style('margin:0 0 2px 2px')
                        if picked['choice'] == CUSTOM:
                            box = ui.input(label='모델 이름', value=picked['typed'],
                                           placeholder='예: gpt-5.6-luna').classes('w-full')
                            box.props('dense outlined stack-label').style('margin-top:6px')
                            box.on_value_change(lambda event: typed(event.value))

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
