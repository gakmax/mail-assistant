"""Launcher for the web screens: one hub, one server, the same data folder.

Lives in the package rather than in packaging/ because two entry points start it —
`MailAssistantTools.exe web` in the shipped build and packaging/entry_web.py in the
spike freeze — and neither should own the wiring.

The window in app.py is still what the installer's shortcut opens; this is the second
way in, not a replacement. UI-PLAN.md 8절 records the decision that is left.
"""
import json
import multiprocessing
import os
from pathlib import Path

from . import services as helpers, webui
from .console import use_utf8
from .hub import Hub
from .report import report
from .settings import DEFAULTS
from .worker import run


def data_folder():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return Path(base) / 'HiworksMailAssistant'


def read_config(path):
    config = dict(DEFAULTS, workbook='')
    if path.exists():
        try:
            config.update(json.loads(path.read_text(encoding='utf-8')))
        except (ValueError, OSError) as exc:
            report('설정 파일 읽기 실패', exc, str(path))
            print(f'설정을 읽지 못해 기본값으로 실행합니다: {type(exc).__name__}: {exc}', flush=True)
    return config


MUTEX = 'Local\\HiworksMailAssistant'
ALREADY_RUNNING = 183       # ERROR_ALREADY_EXISTS


def claim_worker():
    """True when this process may run the collector.

    The window in app.py holds this mutex for exactly this reason, and Hub.start()
    only guards within one process. Without the check, opening the web screens while
    the window is running and pressing 시작 would poll one mailbox twice and have two
    processes writing the same workbook.
    """
    try:
        import win32api
        import win32event
    except ImportError:
        return True, None       # not Windows: nothing else is running either
    handle = win32event.CreateMutex(None, False, MUTEX)
    if win32api.GetLastError() == ALREADY_RUNNING:
        win32api.CloseHandle(handle)
        return False, None
    return True, handle


def main(argv=()):
    # Must come first. Native mode starts pywebview in a second process, and a frozen
    # build without this re-runs this entry in the child instead.
    multiprocessing.freeze_support()
    use_utf8()
    directory = data_folder()
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / 'config.json'
    config = read_config(config_path)
    print(f'데이터 폴더 {directory}', flush=True)
    # services.py imports win32 inside its functions, so this dict builds anywhere and
    # only the calls fail off Windows — which the pages report rather than crash on.
    services = {'login_state': helpers.login_state, 'check_connection': helpers.check_connection,
                'read_password': helpers.read_password, 'save_password': helpers.save_password,
                'delete_password': helpers.delete_password,
                'codex_command': helpers.codex_command,
                'codex_environment': helpers.codex_environment}
    can_start, handle = claim_worker()
    if not can_start:
        print('메일 도우미 창이 이미 실행 중입니다. 화면은 열지만 여기서 수집을 시작할 수는 '
              '없습니다. 창을 닫고 다시 실행하세요.', flush=True)
    try:
        # --native needs pywebview, which requirements.txt does not install and the
        # spec excludes; it stays a spike flag rather than a shipped one.
        webui.serve(directory, config, native='--native' in argv, show='--browser' in argv,
                    hub=Hub(directory, config, run), services=services,
                    config_path=config_path, can_start=can_start)
    finally:
        if handle is not None:
            import win32api
            win32api.CloseHandle(handle)
    return 0
