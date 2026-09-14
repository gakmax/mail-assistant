"""연결 실패와 엑셀 반영 실패의 원인을 확인합니다. Windows에서 실행하세요.

    MailAssistantTools.exe diagnose user@example.com   메일(POP3) 연결 단계별 점검
    MailAssistantTools.exe diagnose --excel            엑셀 시트 열 구성 점검 (설정의 파일)
    MailAssistantTools.exe diagnose --excel "C:\\경로\\파일.xlsx"
    MailAssistantTools.exe diagnose --result [건수]    저장된 분석 결과 확인 (기본 5건)

Windows 자격 증명에 저장된 비밀번호를 사용하고, 없으면 직접 입력받습니다.
비밀번호 자체는 화면에 출력하지 않습니다. 엑셀 점검은 파일을 수정하지 않습니다.
"""
import getpass
import socket
import ssl
import sys

from mail_assistant.console import use_utf8

HOST = 'pop3s.hiworks.com'
PORT = 995


def step(name):
    print(f'\n[{name}]')


def inspect_excel(path):
    """Read-only look at row 1 of every sheet, so a header mismatch is visible."""
    import json
    import os
    from pathlib import Path
    from mail_assistant.core import HEADERS
    from mail_assistant.excel import column_name

    if not path:
        config = Path(os.environ['LOCALAPPDATA']) / 'HiworksMailAssistant' / 'config.json'
        path = json.loads(config.read_text(encoding='utf-8'))['workbook']
        print(f'설정의 엑셀 파일: {path}')
    path = Path(path).resolve()
    if not path.exists():
        print(f'  파일이 없습니다: {path}')
        return

    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = book = None
    try:
        app = win32com.client.DispatchEx('Excel.Application')
        app.DisplayAlerts = False
        app.AutomationSecurity = 3
        book = app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
        names = [sheet.Name for sheet in book.Worksheets]
        step('시트 목록')
        print(f'  {names}')
        for name, headers in HEADERS.items():
            step(f'{name} 시트')
            if name not in names:
                print('  없음: 다음 반영에서 새로 만듭니다.')
                continue
            sheet = book.Worksheets(name)
            current = list(sheet.Range(sheet.Cells(1, 1), sheet.Cells(1, len(headers))).Value[0])
            trimmed = ['' if value is None else str(value).strip() for value in current]
            if trimmed == headers:
                print('  OK: 열 구성 일치')
            elif not any(trimmed):
                print('  1행이 비어 있음: 다음 반영에서 헤더를 씁니다.')
            else:
                for index, (found, expected) in enumerate(zip(trimmed, headers)):
                    if found != expected:
                        print(f'  다름 {column_name(index)}1: 현재 {found!r} / 기대 {expected!r}')
            print(f'  표 {sheet.ListObjects.Count}개, 사용 범위 {sheet.UsedRange.Address}')
            for index in range(1, sheet.ListObjects.Count + 1):
                table = sheet.ListObjects(index)
                print(f'    표 "{table.Name}" 헤더 {table.HeaderRowRange.Address}')
        step('안내')
        print('  열 이름이 다르면 해당 셀의 글자를 기대값으로 되돌리세요.')
        print('  반영된 데이터 행이 없다면 파일을 다른 이름으로 옮기고 다시 시작하는 편이 빠릅니다.')
    finally:
        try:
            if book is not None:
                book.Close(SaveChanges=False)
        finally:
            if app is not None:
                app.Quit()
            book = app = None
            pythoncom.CoUninitialize()


def show_results(limit):
    """Print what Codex actually returned, so a missing Excel row can be traced
    to the analysis instead of the export."""
    import json
    import os
    import sqlite3
    from pathlib import Path

    path = Path(os.environ['LOCALAPPDATA']) / 'HiworksMailAssistant' / 'mail.db'
    if not path.exists():
        print(f'  DB가 없습니다: {path}')
        return
    # Read-only: the running assistant keeps writing to this database.
    db = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute('SELECT id, received, parsed, result, exported, attempts, error '
                      'FROM mail ORDER BY received DESC LIMIT ?', (limit,)).fetchall()
    if not rows:
        print('  수집된 메일이 없습니다.')
    for row in rows:
        parsed = json.loads(row['parsed']) if row['parsed'] else {}
        step(parsed.get('subject', '(제목 없음)'))
        print(f'  메일 ID {row["id"]} / 수신 {row["received"]} / 엑셀 반영 {"완료" if row["exported"] else "대기"}')
        if not row['result']:
            print(f'  분석 결과 없음 (시도 {row["attempts"]}회) {row["error"]}')
            continue
        result = json.loads(row['result'])
        print(f'  종류 {result["category"]} / 우선순위 {result["priority"]} / 일정 {len(result["events"])}건')
        print(f'  reply_needed = {result["reply_needed"]}')
        if result['reply_needed']:
            print(f'  답변 제목 {result["reply_subject"]!r}')
            print(f'  초안 {len(result["reply_draft"])}자: {result["reply_draft"][:60]!r}')
        else:
            print('  → 모델이 답변 불필요로 판단했습니다. 답변 초안 시트에는 행을 만들지 않습니다.')
    db.close()


def main():
    use_utf8()
    if sys.argv[1:2] == ['--result']:
        show_results(int(sys.argv[2]) if len(sys.argv) > 2 else 5)
        return
    if sys.argv[1:2] == ['--excel']:
        inspect_excel(sys.argv[2] if len(sys.argv) > 2 else None)
        return
    email = sys.argv[1] if len(sys.argv) > 1 else input('메일 계정: ').strip()
    host = sys.argv[2] if len(sys.argv) > 2 else HOST
    port = int(sys.argv[3]) if len(sys.argv) > 3 else PORT

    step('1. Windows 자격 증명')
    password = None
    try:
        from mail_assistant.services import read_password
        password = read_password(email)
        print(f'  OK: 저장된 비밀번호 있음 (길이 {len(password)}자)')
        if password != password.strip():
            print('  주의: 앞뒤 공백이 포함되어 있습니다. 다시 입력하세요.')
        if not password.isprintable() or any(ord(c) > 0x7f for c in password):
            print('  주의: 인쇄 불가/비ASCII 문자가 있습니다. 구버전 저장 오류일 수 있으니 재입력하세요.')
    except Exception as exc:
        print(f'  실패: {type(exc).__name__}: {exc}')
        print('  → 도우미 창에서 메일 전용 비밀번호를 다시 입력하고 시작을 누르세요.')
    if not password:
        password = getpass.getpass('  테스트할 메일 전용 비밀번호(입력 없이 Enter=중단): ')
        if not password:
            return

    step('2. DNS 조회')
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)}
        print(f'  OK: {host} -> {", ".join(sorted(addresses))}')
    except Exception as exc:
        print(f'  실패: {type(exc).__name__}: {exc}')
        print('  → 사내 DNS/프록시 문제입니다. 서버 주소 오타도 확인하세요.')
        return

    step('3. TCP + TLS 연결')
    import poplib
    try:
        client = poplib.POP3_SSL(host, port, timeout=30, context=ssl.create_default_context())
        print(f'  OK: {client.getwelcome().decode("latin-1", "replace")}')
    except ssl.SSLCertVerificationError as exc:
        print(f'  실패(인증서): {exc}')
        print('  → 방화벽/백신의 SSL 검사가 가로채는 경우입니다. 예외 등록이 필요합니다.')
        return
    except Exception as exc:
        print(f'  실패: {type(exc).__name__}: {exc}')
        print(f'  → {port} 포트 차단 또는 메일 서비스의 허용 국가/IP 정책일 수 있습니다.')
        return

    try:
        step('4. 로그인')
        try:
            client.user(email)
            client.pass_(password)
            print('  OK: 인증 성공')
        except poplib.error_proto as exc:
            print(f'  실패: 서버 응답 {exc}')
            print('  → 웹메일 설정에서 POP3 사용을 켜고, 앱(메일 전용) 비밀번호를 발급해 쓰는지 확인하세요.')
            return

        step('5. UIDL 지원 여부')
        try:
            listing = client.uidl()[1]
            print(f'  OK: 메일 {len(listing)}건')
        except poplib.error_proto as exc:
            print(f'  실패: 서버 응답 {exc}')
            print('  → 서버가 UIDL을 지원하지 않으면 도우미가 새 메일을 구분할 수 없습니다.')
            return

        step('결과')
        print('  모든 단계 통과. 메일 연결 자체는 정상입니다.')
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


if __name__ == '__main__':
    main()
