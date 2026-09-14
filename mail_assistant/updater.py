"""The update offer as state a screen can draw, and the two answers main() needs after.

app.py kept this in instance attributes and tkinter dialogs. The native window draws
the same states from the web screens, and main() still has to learn which installer to
launch and whether to relaunch with --autostart — so the state lives here rather than
inside whichever screen happens to be showing it. Nothing here imports a toolkit, which
is what lets the tests cover it off Windows.
"""
import threading

from . import update

IDLE, WORKING, READY, FAILED = 'idle', 'working', 'ready', 'failed'


def failure_text(exc):
    """Why the download stopped, in the words the window used. Pure, so it is testable."""
    if isinstance(exc, update.Cancelled):
        return '업데이트를 취소했습니다. 지금 버전을 계속 사용합니다.'
    if isinstance(exc, update.BadDigest):
        return ('내려받은 설치 파일이 손상되었습니다. 설치를 중단했습니다. 잠시 후 다시 '
                '시도하세요. 지금 버전은 그대로 사용할 수 있습니다.')
    if isinstance(exc, update.NoSpace):
        return (f'디스크 공간이 부족해 업데이트를 내려받지 못했습니다. '
                f'약 {update.describe(exc.needed)}의 여유 공간이 필요합니다.')
    return (f'업데이트를 내려받지 못했습니다. {type(exc).__name__}: {exc} — '
            '지금 버전은 그대로 사용할 수 있습니다.')


def progress_text(progress):
    """'12.3MB / 31.2MB', or '' while the manifest gave no size to count towards."""
    total = (progress or {}).get('total') or 0
    if not total:
        return ''
    return f"{update.describe((progress or {}).get('done') or 0)} / {update.describe(total)}"


def offer_line(offer, current):
    """The one line under the version heading: what you have, and what it will cost."""
    if not offer:
        return ''
    text = f'현재 {current}'
    if offer.get('size'):
        text += f" · 내려받기 약 {update.describe(offer['size'])}"
    return text


def consequence(running):
    """What pressing 지금 업데이트 will actually do, which differs while collecting."""
    if running:
        return ('업데이트하려면 도우미를 잠시 멈춰야 합니다. 지금 돌고 있는 메일·분석·엑셀 '
                '작업이 끝난 뒤 설치하며 몇 분 걸릴 수 있습니다. 설정과 메일 기록은 그대로 '
                '유지됩니다.')
    return ('업데이트하면 도우미가 잠시 종료되었다가 자동으로 다시 시작합니다. 설정과 메일 '
            '기록은 그대로 유지됩니다.')


class Updater:
    """One per process. The screens read it; main() reads `installer` and `autostart`."""

    def __init__(self, directory, config, config_path):
        self.directory = directory
        self.config = config
        self.config_path = config_path
        self.offer = None
        self.state = IDLE
        self.message = ''
        self.progress = {'done': 0, 'total': 0}
        self.cancel = threading.Event()
        self.asked = False          # the dialog has had its turn; the button remains
        self.installer = None       # main() launches this, after closing the mutex
        self.autostart = False      # ... and passes this so a collector resumes

    def look(self, force=False):
        """Blocking; run it through nicegui.run.io_bound. update.check never raises."""
        found = update.check(self.config, self.config_path, force=force)
        if found:
            self.offer = found
            self.state = IDLE
            self.message = ''
        return found

    def take(self, running=False):
        """Blocking download. True once main() has everything it needs to install.

        A cancel is not a failure: the offer stays, so the 실행 화면's button can try
        again without another round trip to GitHub.
        """
        if self.offer is None or self.state in (WORKING, READY):
            return False
        self.cancel.clear()
        self.progress = {'done': 0, 'total': int(self.offer.get('size') or 0)}
        self.state = WORKING
        self.message = ''
        try:
            path = update.download(self.offer,
                                   self.directory / 'update' / self.offer['version'],
                                   self.progress, self.cancel)
        except Exception as exc:
            self.state = IDLE if isinstance(exc, update.Cancelled) else FAILED
            self.message = failure_text(exc)
            return False
        self.installer = path
        self.autostart = bool(running)
        self.state = READY
        return True

    def skip(self):
        """Remember the version so the next check passes over it. Returns what was skipped."""
        if self.offer is None:
            return ''
        version = self.offer['version']
        update.remember(self.config_path, {'update_skip': version})
        self.config['update_skip'] = version
        self.offer = None
        self.state = IDLE
        self.message = ''
        return version

    def stop(self):
        self.cancel.set()

    def waiting(self):
        """True while there is something to offer and nothing is being installed."""
        return self.offer is not None and self.state in (IDLE, FAILED)
