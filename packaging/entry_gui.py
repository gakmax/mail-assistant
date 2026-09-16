"""PyInstaller entry point for the windowed build.

A --windowed exe has no console, so an exception raised before the window
opens leaves no trace at all. mail_assistant/__main__.py's
`if __name__ == '__main__'` block does not run when main() is imported, so
every guard has to live here: Discord, a log file the user can send on, and
something on screen.
"""
import sys
import traceback

TITLE = '메일 도우미'
LOG_HINT = '%LOCALAPPDATA%\\HiworksMailAssistant\\startup-error.log'


def write_log(text):
    """A file the user can attach to a message, for when the webhook is off."""
    try:
        import os
        from datetime import datetime
        from pathlib import Path
        folder = Path(os.environ['LOCALAPPDATA']) / 'HiworksMailAssistant'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'startup-error.log').write_text(
            f'{datetime.now().isoformat(timespec="seconds")}\n{text}', encoding='utf-8')
    except Exception:
        pass


def show(message):
    """MessageBoxW, not tkinter: tkinter itself may be what failed."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    except Exception:
        pass


def record(exc):
    text = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        from mail_assistant.report import report
        report('시작 실패', exc)
    except Exception:
        pass
    write_log(text)
    summary = ''.join(traceback.format_exception_only(type(exc), exc)).strip()
    show(f'시작하지 못했어요.\n\n{summary}\n\n자세한 내용은 다음 파일에 있어요:\n{LOG_HINT}')


def run():
    try:
        from mail_assistant.__main__ import main
    except BaseException as exc:
        record(exc)
        return 1
    try:
        main()
    except SystemExit:
        raise  # __main__ uses SystemExit for the "Windows only" message
    except BaseException as exc:
        record(exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(run())
