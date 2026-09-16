"""긴급한 메일이 왔다는 것을 창 밖에서 알린다. 토스트 하나 말고는 아무것도 하지 않는다.

메일 도우미는 켜 두고 보지 않는 프로그램이다 — 그것이 폴링하는 프로그램의 정의다. 그
동안 분석이 '긴급'이라고 판단한 메일이 목록에 쌓여도, 창을 열지 않으면 그 사실을 아는
사람이 없었다. `Store.unnotified()`와 `mark_notified()`는 처음부터 있었고 부르는 데가
한 군데도 없었다.

updater.py와 같은 규칙으로 산다: 어떤 UI 툴킷도 import하지 않고, 무엇을 알릴지 정하는
부분은 순수 함수라 Windows 밖에서 전부 테스트된다. Windows 부분은 실패해도 조용히
False를 돌려준다 — 알림이 안 뜨는 것은 불편이지만, 알림 때문에 수집이 멈추는 것은 고장이다.
"""
import json
import sys

# 이 둘만 창 밖으로 나간다. '보통'까지 알리면 알림은 메일이 왔다는 말과 같아지고, 그것은
# 메일함이 이미 하고 있는 말이다 — 읽는 사람이 끄게 되는 종류의 알림이 그렇게 만들어진다.
LEVELS = ('긴급', '높음')
# 한 주기에 뜨는 토스트는 하나다. 다섯 통을 다섯 번 알리면 네 번은 방해이고, 한 번에
# '긴급 5건'이라고 말하는 쪽이 읽는 사람이 실제로 하는 판단('지금 열까')에 맞다.
TITLE_MAX = 60
# 풍선이 스스로 사라지기를 기다리는 시간. 아이콘을 먼저 지우면 Windows가 풍선을 같이
# 거두어 가는 일이 있어서, 띄운 뒤 이만큼은 트레이에 남겨 둔다.
LINGER = 8.0


def worth_telling(rows, levels=LEVELS):
    """창 밖으로 알릴 만한 메일. Store.unnotified()가 준 것 중 급한 것만.

    아직 처리하지 않은 것만 센다: 알림이 도착하기 전에 이미 읽고 완료로 표시한 메일까지
    알리면, 그 알림은 읽는 사람이 방금 한 일을 모르고 있다는 뜻이 된다.
    """
    found = []
    for row in rows:
        if row['handled']:
            continue
        try:
            result = json.loads(row['result']) if row['result'] else {}
        except ValueError:
            continue
        if result.get('priority') in levels:
            found.append({'id': row['id'], 'priority': result['priority'],
                          'subject': (row['subject'] or '(제목 없음)').strip(),
                          'action': ' '.join(str(result.get('next_action', '')).split())})
    # 긴급이 먼저, 그 다음 받은 순서 그대로 — 제목 한 줄에 들어갈 수 있는 것은 첫 줄뿐이다.
    found.sort(key=lambda item: levels.index(item['priority']))
    return found


def summarise(rows):
    """(제목, 본문) 또는 (None, None). 한 통이면 그 메일을, 여럿이면 세어서 말한다."""
    if not rows:
        return None, None
    first = rows[0]
    subject = first['subject'][:TITLE_MAX]
    if len(rows) == 1:
        title = f"{first['priority']} 메일: {subject}"
        return title, first['action'] or '메일 도우미에서 확인해 주세요.'
    title = f"{first['priority']} 메일 {len(rows)}건"
    return title, f'{subject} 외 {len(rows) - 1}건 — 메일 도우미에서 확인해 주세요.'


def enabled(config):
    """설정의 알림 항목. 없으면 켜진 것으로 본다 — 예전 config.json에는 이 키가 없다."""
    value = (config or {}).get('notify', '1')
    return str(value).strip() not in ('', '0', 'false', 'False')


# 한 프로세스에 트레이 아이콘 하나. 알릴 때마다 클래스를 등록하고 창을 만들고 지우면
# 두 번째 등록이 이미 있다며 실패하고, 창을 지우는 순간 풍선도 같이 사라진다.
_tray = {}

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_ICON, NIF_TIP, NIF_INFO = 0x02, 0x04, 0x10
NIIF_INFO = 0x01


def _icon(instance, icon_path=None):
    """앱의 아이콘, 못 찾으면 None.

    Frozen 빌드에서는 exe 자신이 아이콘을 품고 있으므로 ExtractIconEx가 파일을 따로
    찾지 않아도 된다 — .ico는 패키지에 들어가지 않고 PyInstaller가 exe에 박는다.
    """
    import win32con
    import win32gui
    if icon_path:
        try:
            return win32gui.LoadImage(instance, str(icon_path), win32con.IMAGE_ICON, 0, 0,
                                      win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        except Exception:
            pass
    try:
        large, small = win32gui.ExtractIconEx(sys.executable, 0)
        for extra in small[1:] + large[1:]:
            win32gui.DestroyIcon(extra)
        return (small or large or [None])[0]
    except Exception:
        return None


def _handle(icon_path=None):
    """숨은 창 하나와 트레이 아이콘 하나, 처음 부를 때 만들고 그대로 둔다."""
    if _tray.get('hwnd'):
        return _tray['hwnd'], _tray['icon']
    import win32con
    import win32gui
    instance = win32gui.GetModuleHandle(None)
    klass = win32gui.WNDCLASS()
    klass.hInstance = instance
    klass.lpszClassName = 'MailAssistantNotify'
    # 메시지 펌프는 없다. 풍선을 띄우는 데는 필요하지 않고, 필요한 것은 클릭을 받을
    # 때뿐인데 이 알림은 눌러서 갈 곳이 없다 — 창은 이미 열려 있거나 닫혀 있다.
    klass.lpfnWndProc = {}
    atom = win32gui.RegisterClass(klass)
    hwnd = win32gui.CreateWindow(atom, 'MailAssistant', win32con.WS_OVERLAPPED,
                                 0, 0, 0, 0, 0, 0, instance, None)
    icon = _icon(instance, icon_path) or win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
    win32gui.Shell_NotifyIcon(NIM_ADD, (hwnd, 0, NIF_ICON | NIF_TIP, 0, icon, '메일 도우미'))
    _tray['hwnd'], _tray['icon'] = hwnd, icon
    return hwnd, icon


def send(title, body, icon_path=None):
    """풍선 하나. 성공하면 True, 이 PC가 못 하면 False — 어느 쪽도 예외를 올리지 않는다."""
    if not title:
        return False
    try:
        import win32gui
        hwnd, icon = _handle(icon_path)
        win32gui.Shell_NotifyIcon(NIM_MODIFY, (hwnd, 0, NIF_INFO, 0, icon, '메일 도우미',
                                               str(body or ''), 0, str(title), NIIF_INFO))
        return True
    except Exception:
        # 알림이 안 뜨는 것은 불편이고, 알림 때문에 수집이 멈추는 것은 고장이다.
        return False


def close():
    """트레이에서 아이콘을 거둔다. 프로세스가 끝날 때 한 번."""
    hwnd = _tray.pop('hwnd', None)
    _tray.pop('icon', None)
    if not hwnd:
        return
    try:
        import win32gui
        win32gui.Shell_NotifyIcon(NIM_DELETE, (hwnd, 0))
        win32gui.DestroyWindow(hwnd)
    except Exception:
        pass


def tell(store, account, config, rows=None, icon_path=None):
    """알릴 것이 있으면 알리고, 알린 메일에 표시를 남긴다. 알린 건수를 돌려준다.

    표시는 알림이 **뜨지 않았어도** 남긴다. 창 밖 알림은 지금 이 순간에 대한 것이고,
    다음 주기에 세 시간 전의 긴급 메일을 처음인 양 띄우는 것은 알림이 아니라 잔소리다 —
    목록과 대시보드가 그 메일을 계속 들고 있으므로 잃는 것도 없다.
    """
    waiting = list(rows if rows is not None else store.unnotified(account))
    if not waiting:
        return 0
    store.mark_notified([row['id'] for row in waiting])
    if not enabled(config):
        return 0
    worthy = worth_telling(waiting)
    if not worthy:
        return 0
    title, body = summarise(worthy)
    send(title, body, icon_path)
    return len(worthy)
