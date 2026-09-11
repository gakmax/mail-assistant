"""Console companion to MailAssistant.exe.

One console exe rather than three, because a console entry is needed anyway:
the windowed build has no stdout, so `gui` is what replaces the old
start.cmd when a startup failure has to be read off the screen. diagnose,
sample and demo then cost an elif each and share one bundled runtime.
"""
import sys

USAGE = """메일 도우미 진단 도구

  MailAssistantTools.exe gui                 콘솔을 띄운 채 도우미 실행 (오류 확인용)
  MailAssistantTools.exe selftest            프로그램 구성 요소 점검
  MailAssistantTools.exe version             버전과 폴더 위치
  MailAssistantTools.exe paths               Codex·Node 탐색 결과

  MailAssistantTools.exe diagnose 메일주소     메일(POP3) 연결 단계별 점검
  MailAssistantTools.exe diagnose --excel     엑셀 시트 열 구성 점검
  MailAssistantTools.exe diagnose --result    저장된 분석 결과 확인

  MailAssistantTools.exe sample [경로]        예시 엑셀 파일 만들기
  MailAssistantTools.exe demo                 열려 있는 엑셀에 샘플 행 추가
"""


def data_folder():
    import os
    from pathlib import Path
    return Path(os.environ.get('LOCALAPPDATA', '.')) / 'HiworksMailAssistant'


def show_version():
    from mail_assistant import __version__
    print(f'메일 도우미 {__version__}')
    print(f'실행 파일   {sys.executable}')
    print(f'데이터 폴더 {data_folder()}')
    print(f'고정 빌드   {bool(getattr(sys, "frozen", False))}')
    return 0


def show_paths():
    import shutil
    for name in ('codex', 'node', 'npm'):
        found = shutil.which(name)
        print(f'{name:6} {found or "찾지 못했습니다"}')
    folder = data_folder()
    for name in ('config.json', 'mail.db', 'status.json'):
        path = folder / name
        print(f'{name:12} {"있음" if path.exists() else "없음"}  {path}')
    return 0


def check_shell():
    from win32com.shell import shell, shellcon
    return shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)


def check_com():
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import win32com.client
        return win32com.client.Dispatch.__name__
    finally:
        pythoncom.CoUninitialize()


def check_schema():
    """Forces the jsonschema metaschema data files, which freezing loses quietly."""
    from jsonschema import validate
    from mail_assistant.services import schema
    validate({'category': '문의', 'summary': '', 'requests': '', 'events': [],
              'priority': '보통', 'priority_reason': '', 'next_action': '',
              'reply_needed': False, 'reply_subject': '', 'reply_draft': ''}, schema())
    return 'ok'


def check_sqlite():
    import sqlite3
    with sqlite3.connect('file:selftest?mode=memory&cache=shared', uri=True) as db:
        return db.execute('select sqlite_version()').fetchone()[0]


def selftest():
    """Every import the app makes lazily, so a broken bundle fails here and not later."""
    checks = [
        ('win32api', lambda: __import__('win32api').GetLastError),
        ('win32event', lambda: __import__('win32event').CreateMutex),
        ('win32cred', lambda: __import__('win32cred').CRED_TYPE_GENERIC),
        ('win32com.shell', check_shell),
        ('pythoncom / win32com.client', check_com),
        ('jsonschema 메타스키마', check_schema),
        ('tkinter', lambda: __import__('tkinter').TkVersion),
        ('openpyxl', lambda: __import__('openpyxl').__version__),
        ('sqlite3 (uri)', check_sqlite),
        ('mail_assistant.update', lambda: __import__('mail_assistant.update',
                                                     fromlist=['check']).DEFAULT_REPO),
        # The window and everything it pulls in: __main__ imports it lazily, so a
        # missed module would only show up when the user double-clicks the icon.
        ('mail_assistant.app', lambda: __import__('mail_assistant.app', fromlist=['App']).App.__name__),
    ]
    failed = 0
    for name, probe in checks:
        try:
            print(f'  OK   {name}: {probe()}')
        except Exception as exc:
            failed += 1
            print(f'  실패 {name}: {type(exc).__name__}: {exc}')
    print('\n' + ('모든 항목 정상' if not failed else f'{failed}개 항목 실패'))
    return 1 if failed else 0


def run_script(module, argv):
    """The three scripts read sys.argv positionally, so hand them the shape they expect."""
    saved = sys.argv
    sys.argv = [module] + argv
    try:
        __import__(module).main()
    finally:
        sys.argv = saved
    return 0


def main(argv):
    from mail_assistant.console import use_utf8
    use_utf8()
    command = (argv[0] if argv else 'help').lower().lstrip('-')
    rest = argv[1:]
    if command in ('help', 'h', '?'):
        print(USAGE)
        return 0
    if command == 'version':
        return show_version()
    if command == 'paths':
        return show_paths()
    if command == 'selftest':
        return selftest()
    if command == 'gui':
        from mail_assistant.__main__ import main as gui
        gui()  # no guard: a traceback on the console is the point
        return 0
    if command in ('diagnose', 'sample', 'demo'):
        return run_script(command, rest)
    print(f'알 수 없는 명령입니다: {argv[0]}\n')
    print(USAGE)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
