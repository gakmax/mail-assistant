"""Entry point: crash hooks, single instance, paths, then the app's window.

The window is now the web screens in a pywebview frame — UI-PLAN 8절's open decision,
taken. The tkinter window in app.py has not gone anywhere: `--window` opens it, and a
machine whose pywebview will not load falls back to it rather than showing nothing.
Whichever runs, this file keeps the two things that are easy to get wrong — the mutex
is closed before Setup is launched, and Setup is launched from `finally` so it happens
after the window is gone.
"""
import json
import multiprocessing
import os
import sys
from pathlib import Path

from . import update
from .console import use_utf8
from .report import install_hooks, report
from .settings import DEFAULTS, field_errors

MUTEX = 'Local\\HiworksMailAssistant'
ALREADY_RUNNING = 183       # ERROR_ALREADY_EXISTS


def native_ready():
    """Whether a pywebview frame can be asked for at all.

    A missing WebView2 runtime fails later than this and inside nicegui's own child
    process, which is why `--window` is documented: it is the way out that does not
    need a reinstall.
    """
    if '--window' in sys.argv:
        return False
    try:
        import webview                              # noqa: F401
    except Exception as exc:
        report('네이티브 창 사용 불가', exc)
        return False
    return True


def route_output(directory):
    """Give a windowed build somewhere to print to, before anything tries.

    `MailAssistant.exe` is built console=False, where PyInstaller can leave sys.stdout
    as None — and uvicorn's logger and nicegui's own startup line both write to it. The
    tkinter window never printed, so this only became load-bearing when the web screens
    became the window. Truncated per launch, so the file cannot grow across runs.
    """
    if sys.stdout is not None and sys.stderr is not None:
        use_utf8()
        return
    try:
        stream = open(directory / 'window.log', 'w', encoding='utf-8', buffering=1)
    except OSError:
        stream = open(os.devnull, 'w', encoding='utf-8')
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    use_utf8()


def read_config(config_path, workbook):
    config = dict(DEFAULTS, workbook=workbook)
    if config_path.exists():
        try:
            config.update(json.loads(config_path.read_text(encoding='utf-8')))
        except (ValueError, OSError) as exc:
            report('설정 파일 읽기 실패', exc, str(config_path))
            return config, '설정 파일을 읽지 못했습니다. 설정 화면에서 다시 입력하세요.'
    return config, ''


def helper_services():
    """The calls the screens make into Windows. Kept next to webmain's copy on purpose:
    each entry point says out loud what it hands the screens."""
    from .services import (check_connection, codex_command, codex_environment, delete_password,
                           login_state, read_password, save_password)
    return {'login_state': login_state, 'check_connection': check_connection,
            'read_password': read_password, 'save_password': save_password,
            'delete_password': delete_password, 'codex_command': codex_command,
            'codex_environment': codex_environment}


def open_native(directory, config, config_path, hub, updater, autostart, complaint):
    """The web screens in their own window. Returns when the window closes."""
    from . import webui
    if complaint:
        hub.log(complaint)
    if autostart and config_path.exists():
        # No dialog to refuse with at login, so a bad setting is logged and the 실행
        # screen shows it as a blocker rather than stopping the app from opening.
        errors = field_errors(config)
        if errors:
            hub.log('자동 시작하지 못했습니다 — ' + ' '.join(errors.values()))
        elif hub.start(dict(config)):
            hub.log('자동 시작했습니다.')
    webui.serve(directory, config, hub=hub, services=helper_services(),
                config_path=config_path, native=True, updater=updater)


def open_window(directory, config, config_path, hub, autostart, complaint):
    """The tkinter window. Still the fallback, and still what `--window` asks for."""
    import tkinter as tk
    from tkinter import messagebox

    from .app import App
    if complaint:
        messagebox.showwarning('설정 확인', complaint)
    root = tk.Tk()
    app = App(root, directory, config_path, config, helper_services(), hub,
              autostart=autostart)

    def on_widget_error(kind, value, trace):
        # A windowed build has no console: without this the error leaves no trace.
        report('화면 조작 오류', value)
        messagebox.showerror('메일 도우미',
                             f'화면 처리 중 오류가 발생했습니다.\n{type(value).__name__}: {value}')

    root.report_callback_exception = on_widget_error
    root.protocol('WM_DELETE_WINDOW', app.close)
    root.after(600, app.boot)
    root.mainloop()
    return {'installer': app.pending_installer, 'autostart': app.pending_autostart,
            'silent': (app.offer or {}).get('silent', update.SILENT)}


def main():
    # Must come first. Native mode starts pywebview in a second process, and a frozen
    # build without this re-runs this entry in the child instead of the viewer.
    multiprocessing.freeze_support()
    install_hooks()
    if sys.platform != 'win32':
        raise SystemExit('메일 도우미 실행은 Microsoft Excel이 설치된 Windows에서 지원합니다.')
    import win32api
    import win32event
    from tkinter import messagebox
    from win32com.shell import shell, shellcon

    from .hub import Hub
    from .updater import Updater
    from .worker import run

    mutex = win32event.CreateMutex(None, False, MUTEX)
    if win32api.GetLastError() == ALREADY_RUNNING:
        messagebox.showinfo('메일 도우미',
                            '이미 실행 중입니다. 작업 표시줄에서 메일 도우미를 확인하세요.')
        win32api.CloseHandle(mutex)
        return
    directory = Path(os.environ['LOCALAPPDATA']) / 'HiworksMailAssistant'
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / 'config.json'
    route_output(directory)
    update.sweep(directory / 'update')
    desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    config, complaint = read_config(config_path, str(desktop / '메일 업무관리.xlsx'))

    # The hub owns the worker, so a window is only a view of it.
    hub = Hub(directory, config, run)
    updater = Updater(directory, config, config_path)
    autostart = '--autostart' in sys.argv
    pending = {'installer': None, 'autostart': False, 'silent': update.SILENT}
    try:
        if native_ready():
            open_native(directory, config, config_path, hub, updater, autostart, complaint)
            pending = {'installer': updater.installer, 'autostart': updater.autostart,
                       'silent': (updater.offer or {}).get('silent', update.SILENT)}
        else:
            pending = open_window(directory, config, config_path, hub, autostart, complaint)
    finally:
        hub.close()
        # Close the mutex first. The last handle going away destroys the kernel object
        # even though we are still alive, so Setup's AppMutex check cannot collide with
        # us and abort with exit code 2 — 'user cancelled' — under /SUPPRESSMSGBOXES.
        win32api.CloseHandle(mutex)
        if pending['installer'] is not None:
            try:
                update.launch(pending['installer'], directory / 'update' / 'install.log',
                              pending['silent'], pending['autostart'])
            except Exception as exc:
                report('설치 프로그램 실행 실패', exc)
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        None, '설치 프로그램을 실행하지 못했습니다.\n'
                              '지금 버전은 그대로 사용할 수 있습니다.', '메일 도우미 업데이트', 0x10)
                except Exception:
                    pass


if __name__ == '__main__':
    main()
