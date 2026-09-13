"""Entry point: crash hooks, single instance, paths, then the window in app.py."""
import json
import os
import sys
from pathlib import Path

from . import update
from .report import install_hooks, report
from .settings import DEFAULTS


def main():
    install_hooks()
    if sys.platform != 'win32':
        raise SystemExit('메일 도우미 실행은 Microsoft Excel이 설치된 Windows에서 지원합니다.')
    import tkinter as tk
    import win32api
    import win32event
    from tkinter import messagebox
    from win32com.shell import shell, shellcon
    from .app import App
    from .hub import Hub
    from .services import (check_connection, codex_command, codex_environment, login_state,
                           read_password, save_password)
    from .worker import run

    mutex = win32event.CreateMutex(None, False, 'Local\\HiworksMailAssistant')
    if win32api.GetLastError() == 183:
        messagebox.showinfo('메일 도우미', '이미 실행 중입니다. 작업 표시줄에서 메일 도우미를 확인하세요.')
        win32api.CloseHandle(mutex)
        return
    directory = Path(os.environ['LOCALAPPDATA']) / 'HiworksMailAssistant'
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / 'config.json'
    update.sweep(directory / 'update')
    desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    config = dict(DEFAULTS, workbook=str(desktop / '메일 업무관리.xlsx'))
    if config_path.exists():
        try:
            config.update(json.loads(config_path.read_text(encoding='utf-8')))
        except (ValueError, OSError) as exc:
            report('설정 파일 읽기 실패', exc, str(config_path))
            messagebox.showwarning('설정 확인', '설정 파일을 읽지 못했습니다. 다시 설정하세요.')

    root = tk.Tk()
    # The hub owns the worker, so the window is only a view of it.
    hub = Hub(directory, config, run)
    app = App(root, directory, config_path, config, {
        'login_state': login_state, 'check_connection': check_connection,
        'read_password': read_password, 'save_password': save_password,
        'codex_command': codex_command, 'codex_environment': codex_environment,
    }, hub, autostart='--autostart' in sys.argv)

    def on_widget_error(kind, value, trace):
        # A windowed build has no console: without this the error leaves no trace.
        report('화면 조작 오류', value)
        messagebox.showerror('메일 도우미', f'화면 처리 중 오류가 발생했습니다.\n{type(value).__name__}: {value}')

    root.report_callback_exception = on_widget_error
    root.protocol('WM_DELETE_WINDOW', app.close)
    root.after(600, app.boot)
    try:
        root.mainloop()
    finally:
        # Close the mutex first. The last handle going away destroys the kernel
        # object even though we are still alive, so Setup's AppMutex check cannot
        # collide with us and abort with exit code 2.
        win32api.CloseHandle(mutex)
        if app.pending_installer is not None:
            try:
                update.launch(app.pending_installer, directory / 'update' / 'install.log',
                              app.offer['silent'], app.pending_autostart)
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
