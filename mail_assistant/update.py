"""Update client for public GitHub Releases.

Reads one manifest from the release's stable /releases/latest/download/ URL
rather than api.github.com: that redirect has no rate limit (the API allows 60
anonymous requests an hour per IP, which an office NAT can exhaust), and the
manifest carries the installer hash, so a check costs a single request.

Stdlib only, like report.py, and importable on Linux so the pure parts stay
testable: nothing Windows-specific is touched at module level.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .report import report

DEFAULT_REPO = 'gakmax/mail-assistant'
MANIFEST = 'https://github.com/{}/releases/latest/download/latest.json'
AGENT = f'mail-assistant/{__version__}'
CHECK_TIMEOUT = 15
READ_TIMEOUT = 30
CHECK_SECONDS = 6 * 3600
# How often a screen bothers asking, which is not how often GitHub is asked: check()
# returns None without a request until CHECK_SECONDS has passed, so this only decides
# how soon after that gate opens somebody hears about it. The app sits open all day,
# so without a beat of some kind the only checks that ever happen are at launch.
WATCH_SECONDS = 30 * 60
# /SILENT rather than /VERYSILENT: once our window closes, the installer's
# progress bar is the only sign anything is happening. A blank desktop reads
# as a crash. The manifest can override this without shipping a new client.
SILENT = ('/SILENT', '/NOCANCEL', '/NORESTART', '/SUPPRESSMSGBOXES')
CHUNK = 262144
MAX_BYTES = 200 * 1024 * 1024
# The installer writes roughly its own size again while unpacking.
HEADROOM = 3
STALE_SECONDS = 7 * 86400
SETTLE_SECONDS = 300
HEX = set('0123456789abcdef')


class Cancelled(Exception):
    """The user pressed 취소 during the download."""


class BadDigest(Exception):
    """The downloaded file is not what the manifest says it is."""

    def __init__(self, expected, actual):
        super().__init__(f'{expected} != {actual}')
        self.expected, self.actual = expected, actual


class NoSpace(Exception):
    """Not enough free space to download and unpack safely."""

    def __init__(self, needed):
        super().__init__(describe(needed))
        self.needed = needed


class TooLarge(Exception):
    """The asset exceeded MAX_BYTES — a broken release, not a broken download."""


def parse_version(text):
    """'v1.2.0' -> (1, 2, 0). None for anything that is not a plain dotted number."""
    parts = str(text or '').strip().lstrip('vV').split('.')
    if not 1 <= len(parts) <= 4 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def newer(remote, local):
    """Strictly greater, zero-padded to equal length. False if either is unknown."""
    if not remote or not local:
        return False
    width = max(len(remote), len(local))
    pad = (0,) * width
    return (remote + pad)[:width] > (local + pad)[:width]


def label(version):
    """(1, 2, 0) -> '1.2.0'."""
    return '.'.join(str(part) for part in version)


def summarize(body, lines=12, chars=600):
    """Release notes trimmed for a dialog."""
    text = str(body or '').replace('\r\n', '\n').strip()
    if not text:
        return '변경 내용이 제공되지 않았습니다.'
    trimmed = '\n'.join(text.split('\n')[:lines])
    if len(trimmed) > chars:
        trimmed = trimmed[:chars].rstrip()
    return trimmed + ('\n…' if trimmed != text else '')


def describe(size):
    """24117248 -> '23.0MB'."""
    value = float(size or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f}B' if unit == 'B' else f'{value:.1f}{unit}'
        value /= 1024
    return f'{value:.1f}GB'


def digest(path):
    """Streaming sha256 of a file, lowercase hex."""
    hasher = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def offer_from(data, local, skip=''):
    """Validate a manifest and decide whether it is worth offering. Pure.

    Returns an offer dict, or None when there is nothing to do. Raises
    ValueError only when the manifest itself is malformed, so a release-process
    mistake gets reported instead of silently doing nothing.
    """
    if not isinstance(data, dict):
        raise ValueError('매니페스트가 객체가 아닙니다.')
    remote = parse_version(data.get('version'))
    if remote is None:
        raise ValueError(f"version={data.get('version')!r}")
    if not newer(remote, local):
        return None
    version = label(remote)
    if version == str(skip or ''):
        return None
    installer = data.get('installer')
    if not isinstance(installer, dict):
        raise ValueError('installer 항목이 없습니다.')
    url, sha = str(installer.get('url') or ''), str(installer.get('sha256') or '').lower()
    name = str(installer.get('name') or '')
    if not url.startswith('https://'):
        raise ValueError(f'installer.url이 https가 아닙니다: {url[:60]!r}')
    if len(sha) != 64 or set(sha) - HEX:
        raise ValueError('installer.sha256이 64자리 16진수가 아닙니다.')
    size = installer.get('size')
    contract = data.get('install') if isinstance(data.get('install'), dict) else {}
    silent = contract.get('silent_args')
    return {
        'version': version,
        'name': name or url.rsplit('/', 1)[-1],
        'url': url,
        'sha256': sha,
        'size': int(size) if isinstance(size, int) and size > 0 else 0,
        'notes': summarize(data.get('notes')),
        'silent': tuple(str(arg) for arg in silent) if isinstance(silent, list) and silent else SILENT,
    }


def settings(config):
    """Env wins, then config.json, then defaults — the precedence report.webhook() uses."""
    raw = os.environ.get('MAIL_ASSISTANT_UPDATE')
    forced = False
    if raw is not None:
        value = raw.strip().lower()
        forced = value == 'force'
        enabled = forced or value not in ('0', 'false', 'no', 'off')
    else:
        enabled = config.get('update') is not False
    checked = config.get('update_checked')
    return {
        'enabled': enabled,
        'forced': forced,
        # A source checkout has no installed copy to replace.
        'frozen': bool(getattr(sys, 'frozen', False)),
        'repo': (os.environ.get('MAIL_ASSISTANT_UPDATE_REPO')
                 or config.get('update_repo') or DEFAULT_REPO),
        'skip': str(config.get('update_skip') or ''),
        'checked': float(checked) if isinstance(checked, (int, float)) else 0.0,
    }


def remember(config_path, values):
    """Re-read, merge, atomic replace. Never clobbers keys we do not own."""
    try:
        path = Path(config_path)
        current = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(current, dict):
            current = {}
        current.update(values)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
    except (OSError, ValueError):
        pass


def stamp(config_path):
    """When a check last actually reached GitHub, as remember() wrote it. 0.0 if never.

    Read back from the file rather than kept in memory: remember() is what records it,
    and it is also what a second process (the tools exe) would have updated.
    """
    try:
        data = json.loads(Path(config_path).read_text(encoding='utf-8'))
        value = data.get('update_checked') if isinstance(data, dict) else None
        return float(value) if isinstance(value, (int, float)) else 0.0
    except (OSError, ValueError, TypeError):
        return 0.0


def fetch(url, timeout, accept='application/json'):
    """GET with the User-Agent GitHub requires. Raises on anything but success."""
    request = urllib.request.Request(url, headers={'User-Agent': AGENT, 'Accept': accept})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_manifest(repo, timeout=CHECK_TIMEOUT):
    """The newest release's latest.json, via the stable redirect."""
    return json.loads(fetch(MANIFEST.format(repo), timeout).decode('utf-8'))


def check(config, config_path, force=False):
    """The whole gated check. Returns an offer dict or None. Never raises."""
    try:
        options = settings(config)
        if not options['enabled']:
            return None
        if not options['frozen'] and not (force or options['forced']):
            return None
        now = time.time()
        if not force and now - options['checked'] < CHECK_SECONDS:
            return None
        try:
            data = fetch_manifest(options['repo'])
        except urllib.error.HTTPError as exc:
            # 404 before the first release is published is normal; report the rest.
            if exc.code != 404:
                report('업데이트 확인 실패', exc, f'HTTP {exc.code}')
            remember(config_path, {'update_checked': now})
            return None
        except Exception as exc:
            report('업데이트 확인 실패', exc)
            return None
        remember(config_path, {'update_checked': now})
        try:
            return offer_from(data, parse_version(__version__), options['skip'])
        except ValueError as exc:
            report('업데이트 정보 형식 오류', exc)
            return None
    except Exception as exc:
        report('업데이트 확인 오류', exc)
        return None


def download(offer, folder, progress=None, cancel=None):
    """Stream, verify, and only then name the file setup.exe. Raises on failure."""
    progress = progress if progress is not None else {}
    work = Path(folder)
    work.mkdir(parents=True, exist_ok=True)
    target, part = work / 'setup.exe', work / 'setup.part'
    expected = offer['sha256']

    if target.exists():
        try:
            if digest(target) == expected:
                return target
        except OSError:
            pass
        target.unlink()

    size = int(offer.get('size') or 0)
    if size and shutil.disk_usage(work).free < size * HEADROOM:
        raise NoSpace(size * HEADROOM)

    progress['total'], progress['done'] = size, 0
    hasher, received = hashlib.sha256(), 0
    try:
        request = urllib.request.Request(offer['url'], headers={'User-Agent': AGENT,
                                                                'Accept': 'application/octet-stream'})
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response, part.open('wb') as handle:
            declared = response.getheader('Content-Length')
            if declared and str(declared).isdigit():
                progress['total'] = int(declared)
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_BYTES:
                    raise TooLarge(received)
                hasher.update(chunk)
                handle.write(chunk)
                progress['done'] = received
    except BaseException:
        # The handle is closed by now, so Windows lets us remove the partial file.
        part.unlink(missing_ok=True)
        raise

    actual = hasher.hexdigest()
    if actual != expected:
        part.unlink(missing_ok=True)
        raise BadDigest(expected, actual)
    os.replace(part, target)
    remember(work / 'pending.json',
             {'version': offer['version'], 'from': __version__, 'launched': time.time()})
    return target


def launch(installer, log_path, silent=SILENT, autostart=False):
    """Start the silent installer detached. Call only after the mutex handle is closed.

    Passing /TASKS or /MERGETASKS would override the desktop-icon and autostart
    choices Inno restores via UsePreviousTasks, so we never send them.
    """
    installer = Path(installer)
    command = [str(installer), *silent, f'/LOG={log_path}',
               '/relaunch=autostart' if autostart else '/relaunch=1']
    # DETACHED_PROCESS is Windows-only, and must not be ORed with CREATE_NO_WINDOW.
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0)
    subprocess.Popen(command, cwd=str(installer.parent), close_fds=True, creationflags=flags)
    return command


def tail(path, lines=15):
    """The end of the installer log, for a failure report."""
    try:
        return '\n'.join(Path(path).read_text(encoding='utf-8', errors='replace').splitlines()[-lines:])
    except OSError:
        return '설치 기록 없음'


def sweep(folder):
    """Report a silent install that did not take, and clear leftovers. Never raises."""
    try:
        root = Path(folder)
        if not root.is_dir():
            return
        local, now = parse_version(__version__), time.time()
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            marker = child / 'pending.json'
            try:
                record = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else None
            except (OSError, ValueError):
                record = None
            if not isinstance(record, dict):
                if now - child.stat().st_mtime > STALE_SECONDS:
                    shutil.rmtree(child, ignore_errors=True)
                continue
            launched = record.get('launched')
            launched = float(launched) if isinstance(launched, (int, float)) else 0.0
            if newer(parse_version(record.get('version')), local):
                if now - launched <= SETTLE_SECONDS:
                    continue  # the installer may still be running
                report('업데이트 설치 실패', note=f"{record.get('version')} 설치 후에도 {__version__}\n"
                                              + tail(child / 'install.log'))
            shutil.rmtree(child, ignore_errors=True)
    except Exception:
        pass
