"""Crash reports to a Discord webhook. One installed user, so no server or auth."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__

# The repository is public, so the URL never lives in source. CI writes _secrets.py
# from a repository secret at build time; a source checkout uses the env var or config.
DEFAULT_WEBHOOK = ''
try:
    from ._secrets import WEBHOOK as DEFAULT_WEBHOOK
except ImportError:
    pass
KST = timezone(timedelta(hours=9))
REPEAT_SECONDS = 1800
HOURLY_LIMIT = 20
TRACE_LIMIT = 3000
MAIL_PATTERN = re.compile(r'[\w.+-]+@[\w.-]+\.\w+')

_sent = {}
_history = []
_secrets = []
_lock = threading.Lock()


def webhook():
    """Environment wins, then config.json, then the built-in default. Empty disables."""
    override = os.environ.get('MAIL_ASSISTANT_WEBHOOK')
    if override is not None:
        return override.strip()
    configured = None
    try:
        path = Path(os.environ['LOCALAPPDATA']) / 'HiworksMailAssistant' / 'config.json'
        configured = json.loads(path.read_text(encoding='utf-8')).get('webhook')
    except (KeyError, OSError, ValueError, AttributeError):
        pass
    return (DEFAULT_WEBHOOK if configured is None else str(configured)).strip()


def remember_secret(value):
    """The mail password must not reach Discord, not even inside a traceback."""
    if value and len(value) >= 4:
        with _lock:
            if value not in _secrets:
                _secrets.append(value)


def mask_mail(match):
    local, _, domain = match.group(0).partition('@')
    return local[:2] + '***@' + domain


def scrub(text):
    with _lock:
        secrets = list(_secrets)
    for secret in secrets:
        text = text.replace(secret, '***')
    return MAIL_PATTERN.sub(mask_mail, text)


def machine():
    return os.environ.get('COMPUTERNAME') or platform.node() or '알 수 없음'


def allowed(key):
    """Polls retry every few minutes; one report per problem per half hour is plenty."""
    moment = time.time()
    with _lock:
        _history[:] = [stamp for stamp in _history if moment - stamp < 3600]
        if len(_history) >= HOURLY_LIMIT or moment - _sent.get(key, 0) < REPEAT_SECONDS:
            return False
        _sent[key] = moment
        _history.append(moment)
        return True


def signature(stage, exc, note):
    key = f'{stage}|{type(exc).__name__ if exc is not None else note}'
    frames = traceback.extract_tb(exc.__traceback__) if exc is not None else []
    return f'{key}|{frames[-1].filename}:{frames[-1].lineno}' if frames else key


def payload(stage, exc, note):
    summary = scrub(f'{type(exc).__name__}: {exc}'.strip()) if exc is not None else (note or '원인 미상')
    trace = ''
    if exc is not None:
        trace = scrub(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))).strip()
        if len(trace) > TRACE_LIMIT:
            trace = '…(앞부분 생략)\n' + trace[-TRACE_LIMIT:]
    fields = [
        {'name': 'PC', 'value': machine()[:200], 'inline': True},
        {'name': '시각', 'value': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST'), 'inline': True},
        {'name': '버전', 'value': f'{__version__} · py{sys.version_info.major}.{sys.version_info.minor}', 'inline': True},
    ]
    if note and exc is not None:
        fields.append({'name': '상황', 'value': scrub(note)[:1000], 'inline': False})
    description = f'**{summary[:400]}**\n```\n{trace}\n```' if trace else summary[:1500]
    return {'username': '메일 도우미', 'embeds': [{'title': f'⚠️ {stage}'[:250], 'color': 0xE03131,
                                               'description': description, 'fields': fields}]}


def deliver(body):
    request = urllib.request.Request(webhook(), data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'mail-assistant'})
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def report(stage, exc=None, note=None):
    """Fire and forget. A dead webhook must never disturb the assistant itself."""
    try:
        if not webhook() or not allowed(signature(stage, exc, note)):
            return
        body = payload(stage, exc, note)
    except Exception:
        return
    threading.Thread(target=quietly, args=(body,), daemon=True).start()


def quietly(body):
    try:
        deliver(body)
    except Exception:
        pass


def install_hooks():
    """Catch what no except block saw: startup crashes and stray threads."""
    previous = sys.excepthook

    def on_exception(kind, value, trace):
        report('처리되지 않은 오류', value)
        # A windowed build has no usable stderr; the default hook would fail on it.
        if sys.stderr is not None:
            previous(kind, value, trace)

    def on_thread_exception(args):
        if args.exc_value is not None and not issubclass(args.exc_type, SystemExit):
            report(f'스레드 오류 ({getattr(args.thread, "name", "?")})', args.exc_value)

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception
