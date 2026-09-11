"""Windows Excel smoke test: no mailbox access and no Codex usage."""
import json
import sys
from datetime import datetime


def main():
    if sys.platform != 'win32':
        raise SystemExit('이 샘플은 Excel이 설치된 Windows에서 실행하세요.')
    from pathlib import Path
    from win32com.shell import shell, shellcon
    from mail_assistant.excel import Excel
    desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    path = desktop / '메일 업무관리 샘플.xlsx'
    ident = datetime.now().strftime('sample-%Y%m%d-%H%M%S-%f')
    parsed = {'sender': '샘플 거래처 <sample@example.com>', 'subject': '견적 검토 요청 (샘플)', 'attachments': []}
    result = {'category': '견적·계약', 'summary': '견적 검토와 회신을 요청한 샘플 메일입니다.',
              'requests': '견적을 확인한 뒤 회신', 'events': [
                  {'title': '견적 회신', 'start': '', 'deadline': '', 'evidence': '다음 주쯤 회신 부탁드립니다.', 'needs_review': True}],
              'priority': '보통', 'priority_reason': '확정 마감일이 없어 일정 확인 필요', 'next_action': '견적과 회신 기한 확인',
              'reply_needed': True, 'reply_subject': 'Re: 견적 검토 요청',
              'reply_draft': '안녕하세요. 보내주신 내용을 확인했습니다. 회신이 필요한 정확한 기한을 알려주시면 검토에 참고하겠습니다.'}
    row = {'id': ident, 'received': datetime.now().astimezone().isoformat(),
           'parsed': json.dumps(parsed), 'result': json.dumps(result)}
    Excel(path).update([row], {'안내': '샘플 데이터: 메일 수신 및 AI 분석을 실행하지 않았습니다.'})
    print(f'샘플 반영 완료: {path}')
    print('파일을 열고 이 명령을 다시 실행하면 새 행이 추가됩니다. 기존 처리 상태/수정본은 유지됩니다.')


if __name__ == '__main__':
    main()
