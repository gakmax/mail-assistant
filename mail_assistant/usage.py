"""Codex 사용량 — the account's own rate limits, as state a screen can draw.

Beside updater.py and for the same reasons: both windows draw it, the beat has to be
one place, and nothing here imports a toolkit, which is what lets the tests cover every
line off Windows. The numbers come from `services.codex_usage()`, which asks the CLI —
they are never guessed from how much this app has run.
"""
import threading
import time
from datetime import datetime

from .core import KST

# How old a reading may be before the beat asks again. A usage read costs no model
# quota, but it is still a process launch, and the number it returns moves with the
# analyses this app itself starts — minutes apart, never seconds.
READ_SECONDS = 300.0
# Past this, the tooltip says when the reading was taken rather than implying it is now.
STALE_SECONDS = 1800.0

CALM, WARN, FULL = 'calm', 'warn', 'full'
WARN_PERCENT = 70
FULL_PERCENT = 90

# What ChatGPT calls the plan, in the word the account holder sees on the web.
PLANS = {'free': 'Free', 'go': 'Go', 'plus': 'Plus', 'pro': 'Pro', 'prolite': 'Pro Lite',
         'team': 'Team', 'business': 'Business', 'enterprise': 'Enterprise', 'edu': 'Edu'}


def as_percent(value):
    """0~100. Codex reports a float; the screen has one line and no decimal to spend."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, number))


def window_of(data):
    """One rate-limit window, or None. Pure, so a shape change is a test and not a crash."""
    if not isinstance(data, dict) or data.get('usedPercent') is None:
        return None
    minutes = data.get('windowDurationMins')
    resets = data.get('resetsAt')
    return {'percent': as_percent(data.get('usedPercent')),
            'minutes': int(minutes) if isinstance(minutes, (int, float)) else 0,
            'resets': int(resets) if isinstance(resets, (int, float)) else 0}


def snapshot(payload, at=None):
    """`services.codex_usage()`'s answer, reduced to the six numbers a screen shows.

    {} for anything that is not a rate-limit payload: a missing reading draws nothing,
    which is the rule badge_text() and the 분석 실패 card already follow.
    """
    data = payload.get('rateLimits') if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {}
    primary, secondary = window_of(data.get('primary')), window_of(data.get('secondary'))
    if primary is None and secondary is None:
        return {}
    allowed = payload.get('ordinaryUsageAllowed')
    return {'primary': primary, 'secondary': secondary,
            'plan': str(data.get('planType') or ''),
            # Two ways of being out: the backend says so outright, or it names the limit
            # that was reached. Never inferred from 100% — a percentage is a measurement
            # and the permission is the backend's own answer.
            'blocked': bool(data.get('rateLimitReachedType')) or allowed is False,
            'at': time.time() if at is None else float(at)}


def window_name(minutes):
    """'5시간 한도' — the window Codex gave, in the words a non-developer reads."""
    if not minutes:
        return '사용 한도'
    if minutes % 10080 == 0:
        weeks = minutes // 10080
        return '주간 한도' if weeks == 1 else f'{weeks}주 한도'
    if minutes % 1440 == 0:
        days = minutes // 1440
        return '하루 한도' if days == 1 else f'{days}일 한도'
    if minutes % 60 == 0:
        return f'{minutes // 60}시간 한도'
    return f'{minutes}분 한도'


def clock_text(stamp, now=None):
    """'14:16', or '9월 21일 14:16' when it is not today. Never a Korean strftime.

    Windows encodes a format string with the locale codec, so '%m월' raises
    UnicodeEncodeError on any PC that is not set to Korean — the same crash
    updater.checked_text() and day_title() are both written around.
    """
    if not stamp:
        return ''
    try:
        when = datetime.fromtimestamp(float(stamp), KST)
    except (TypeError, ValueError, OSError, OverflowError):
        return ''
    today = datetime.fromtimestamp(time.time() if now is None else float(now), KST).date()
    clock = when.strftime('%H:%M')
    if when.date() == today:
        return clock
    return f'{when.month}월 {when.day}일 {clock}'


def window_line(title, window, now=None):
    """'5시간 한도 32% 사용 · 14:16 초기화', or '' when Codex reported no such window."""
    if not window:
        return ''
    line = f"{title} {window['percent']}% 사용"
    reset = clock_text(window.get('resets'), now)
    return f'{line} · {reset} 초기화' if reset else line


def worst(snap):
    """The window that will stop the next analysis first, which is the one to show."""
    windows = [window for window in (snap.get('primary'), snap.get('secondary')) if window]
    return max(windows, key=lambda window: window['percent']) if windows else None


def level_of(percent, blocked=False):
    if blocked or percent >= FULL_PERCENT:
        return FULL
    return WARN if percent >= WARN_PERCENT else CALM


def plan_text(plan):
    name = PLANS.get(str(plan or '').lower())
    return f'ChatGPT {name} 요금제' if name else ''


def taken_text(at, now=None):
    """When the reading was taken — said out loud only once it is old enough to matter."""
    if not at:
        return ''
    moment = time.time() if now is None else float(now)
    if moment - float(at) < STALE_SECONDS:
        return ''
    return f'{clock_text(at, now)} 기준이에요.'


def view(snap, message='', now=None, analysed=None):
    """What the header chip and the 실행 lamp both draw.

    `known` False means the chip is not drawn at all. A Codex this app has not managed
    to ask is not a Codex at 0% — the same reason '건너뛴 버전이 없습니다' was replaced.
    """
    snap = snap or {}
    top = worst(snap)
    if not top:
        return {'known': False, 'text': '', 'percent': 0, 'level': CALM,
                'lines': [line for line in (message,) if line]}
    blocked = bool(snap.get('blocked'))
    lines = [window_line(window_name((snap.get('primary') or {}).get('minutes')),
                         snap.get('primary'), now),
             window_line(window_name((snap.get('secondary') or {}).get('minutes')),
                         snap.get('secondary'), now)]
    if blocked:
        after = clock_text(top.get('resets'), now)
        lines.append(f'{after}에 다시 쓸 수 있어요.' if after
                     else '한도가 풀리면 다시 분석해요.')
    # 통당 값은 5시간 창으로 잰다 — 주간 창은 며칠에 걸쳐 차므로 '지금 속도'가 아니다.
    reading = capacity((snap.get('primary') or {}).get('percent'), analysed) \
        if analysed is not None else None
    lines += [capacity_line(reading), plan_text(snap.get('plan')),
              taken_text(snap.get('at'), now), message]
    return {'known': True,
            'text': 'Codex 한도 도달' if blocked else f"Codex 사용량 {top['percent']}%",
            'percent': top['percent'],
            'level': level_of(top['percent'], blocked),
            'capacity': reading,
            'lines': [line for line in lines if line]}


# ── 이 계정으로 몇 통이나 되는가 ─────────────────────────────────────────────
# OpenAI 는 Free·Go 의 Codex 한도를 숫자로 내놓지 않는다(Plus 이상만 표에 있다). 그래서
# '하루 몇 통 되나'에 답하는 방법은 하나뿐이다: 계정이 스스로 말하는 사용률과, 이 앱이
# 그 창에서 실제로 분석한 통 수. 둘을 나누면 통당 값이 나오고, 그것은 요금제가 무엇이든
# 맞는 숫자다 — 남이 발표해 주기를 기다릴 필요가 없다.
#
# 정수 퍼센트로 하는 나눗셈이라 쓴 양이 적을 때의 답은 대부분 반올림이다. 그 아래에서는
# 아무 말도 하지 않는다: 30통이라고 들었다가 12통에서 멈추는 것보다 아직 모른다고 듣는
# 편이 낫고, 그것이 badge_text()의 0과 recheck()의 '물어보지 못했다'가 이미 따르는 규칙이다.
MIN_PERCENT = 5


def window_since(window):
    """이 창이 열린 시각(epoch), 또는 None. resets 와 길이가 둘 다 있어야 답이 된다."""
    if not window or not window.get('resets') or not window.get('minutes'):
        return None
    return float(window['resets']) - float(window['minutes']) * 60


def capacity(percent, analysed):
    """이 창에서 앞으로 몇 통쯤 더 되는지. 재기에 이르면 None.

    analysed 는 이 창에서 분석을 마친 메일 수다. 브리핑·초안·번역·상담도 같은 한도를
    쓰면서 이 수에는 잡히지 않으므로, 통당 값은 실제보다 비싸게 나오고 따라서 남은 통
    수는 적게 나온다 — 틀리는 방향이 안전한 쪽이라 그대로 둔다.
    """
    try:
        percent, analysed = int(percent), int(analysed)
    except (TypeError, ValueError):
        return None
    if analysed <= 0 or percent < MIN_PERCENT:
        return None
    total = int(analysed * 100 / percent)
    return {'analysed': analysed, 'total': total, 'left': max(0, total - analysed)}


def capacity_line(reading):
    """'이 창에서 12통 분석 · 이 속도면 30통쯤에서 한도' — 못 재면 ''."""
    if not reading:
        return ''
    return (f"이 창에서 {reading['analysed']}통 분석 · "
            f"이 속도면 {reading['total']}통쯤에서 한도에 닿아요")


CAPACITY_TIP = ('계정이 말한 사용률을 이 앱이 그 창에서 분석한 통 수로 나눈 값이에요. '
                '브리핑·초안·번역도 같은 한도를 쓰지만 이 통 수에는 안 잡혀서, 실제로는 '
                '조금 더 갈 수 있어요')


def read_error(exc):
    """Why the last read failed, in one line. The chip keeps the older reading beside it."""
    if isinstance(exc, TimeoutError):
        return 'Codex 사용량을 확인하지 못했어요. 응답이 없어요.'
    return f'Codex 사용량을 확인하지 못했어요. {type(exc).__name__}: {exc}'


class Meter:
    """One per process. The screens read `view()`; the beat calls `watch()`.

    `read` is injected rather than imported so the tests drive every state without a
    Codex, exactly as Updater takes its directory and config.
    """

    def __init__(self, read=None, interval=READ_SECONDS):
        self.read = read
        self.interval = interval
        self.snapshot = {}
        self.message = ''
        self.at = 0.0               # monotonic of the last attempt, failures included
        self.busy = False
        self.lock = threading.Lock()

    def due(self, now=None):
        moment = time.monotonic() if now is None else now
        return not self.busy and (self.at <= 0 or moment - self.at >= self.interval)

    def look(self):
        """Blocking; run it through nicegui.run.io_bound. Never raises.

        A failure keeps whatever was read last: a reading from ten minutes ago with its
        own timestamp beside it beats an empty chip, and the message says what happened.
        """
        if self.read is None:
            return None
        with self.lock:
            if self.busy:
                return None         # another page's beat is already asking
            self.busy = True
        try:
            found = snapshot(self.read())
        except Exception as exc:
            self.message = read_error(exc)
            found = {}
        finally:
            self.at = time.monotonic()
            self.busy = False
        if found:
            self.snapshot, self.message = found, ''
        return found or None

    def watch(self):
        """The beat's own look — None on every beat but the one that asks."""
        return self.look() if self.due() else None

    def since(self):
        """5시간 창이 열린 시각(epoch), 또는 None — 통당 값을 재는 구간의 시작이다."""
        return window_since((self.snapshot or {}).get('primary'))

    def view(self, now=None, analysed=None):
        return view(self.snapshot, self.message, now, analysed)
