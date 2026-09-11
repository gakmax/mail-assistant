"""Settings validation, kept out of the GUI so it can be tested without tkinter."""
# PureWindowsPath, not Path: the assistant is Windows-only and the check must not
# depend on the platform the tests happen to run on.
from pathlib import PureWindowsPath

DEFAULTS = {'host': 'pop3s.hiworks.com', 'port': 995, 'interval': 180, 'email': '', 'model': ''}
FIELDS = (('email', '메일 계정'), ('password', '메일 전용 비밀번호'), ('host', '수신 서버'),
          ('port', 'SSL 포트'), ('interval', '확인 간격(초, 최소 60)'), ('workbook', '엑셀 파일'),
          ('model', 'Codex 모델(비우면 기본값)'))


def normalize(values):
    """Trimmed, typed settings. Raises ValueError with a message meant for the user."""
    updated = {key: str(value).strip() for key, value in values.items() if key != 'password'}
    for key in ('port', 'interval'):
        try:
            updated[key] = int(updated[key])
        except (KeyError, ValueError):
            raise ValueError('SSL 포트와 확인 간격은 숫자로 입력하세요.')
    if '@' not in updated.get('email', '') or not updated.get('host'):
        raise ValueError('메일 계정과 수신 서버를 입력하세요.')
    if updated['interval'] < 60 or not 1 <= updated['port'] <= 65535:
        raise ValueError('확인 간격은 60초 이상, 포트는 1~65535로 설정하세요.')
    path = PureWindowsPath(updated.get('workbook', ''))
    if not path.is_absolute() or path.suffix.lower() != '.xlsx':
        raise ValueError('엑셀 파일은 절대 경로의 .xlsx 파일로 지정하세요.')
    return updated
