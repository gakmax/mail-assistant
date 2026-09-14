"""Settings validation, kept out of the GUI so it can be tested without tkinter."""
import json
import os
# PureWindowsPath, not Path: the assistant is Windows-only and the check must not
# depend on the platform the tests happen to run on.
from pathlib import Path, PureWindowsPath

DEFAULTS = {'host': 'pop3s.hiworks.com', 'port': 995, 'interval': 180, 'email': '', 'model': ''}
FIELDS = (('email', '메일 계정'), ('password', '메일 전용 비밀번호'), ('host', '수신 서버'),
          ('port', 'SSL 포트'), ('interval', '확인 간격(초, 최소 60)'), ('workbook', '엑셀 파일'),
          ('model', 'Codex 모델'))

# The radio row that opens the free-text box. Never written to config.json: the box's
# own value is, so a saved model is always a plain slug the CLI can take.
CUSTOM = '__custom__'
DEFAULT_MODEL = ('', '기본값', 'Codex CLI가 정한 모델을 그대로 씁니다.')
CUSTOM_MODEL = (CUSTOM, '직접 입력', '목록에 없는 모델 이름을 직접 적습니다.')
# Codex writes this beside its own config whenever it runs, and it holds the models
# *this account* may use. Reading it beats shipping a list: the slugs turn over every
# few weeks, and a stale one fails every analysis with '모델을 지원하지 않습니다'.
MODELS_CACHE = 'models_cache.json'
NO_MODELS = ('Codex 모델 목록을 찾지 못했습니다. Codex를 한 번 실행하면 '
             '이 자리에 선택할 수 있는 모델이 나타납니다.')


def field_errors(values):
    """{field: message} so a form can mark the field instead of raising one dialog.

    normalize() stays the gate for saving; a test holds the two to the same verdict.
    """
    errors = {}
    if '@' not in str(values.get('email', '')).strip():
        errors['email'] = '메일 계정을 user@example.com 형태로 입력하세요.'
    if not str(values.get('host', '')).strip():
        errors['host'] = '수신 서버를 입력하세요.'
    for key, label, low, high in (('port', 'SSL 포트', 1, 65535),
                                  ('interval', '확인 간격', 60, 86400)):
        raw = str(values.get(key, '')).strip()
        try:
            number = int(raw)
        except ValueError:
            errors[key] = f'{label}은 숫자로 입력하세요.'
            continue
        if not low <= number <= high:
            errors[key] = f'{label}은 {low}~{high} 사이로 입력하세요.'
    path = PureWindowsPath(str(values.get('workbook', '')).strip())
    if not path.is_absolute() or path.suffix.lower() != '.xlsx':
        errors['workbook'] = '엑셀 파일은 절대 경로의 .xlsx 파일로 지정하세요.'
    if not model_ok(values.get('model', '')):
        errors['model'] = '모델 이름은 공백 없이 한 단어로 입력하세요.'
    return errors


def model_ok(value):
    """A model is optional, but what is there becomes one `--model` argument."""
    text = str(value or '').strip()
    return text == '' or len(text.split()) == 1


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
    if not model_ok(updated.get('model', '')):
        raise ValueError('모델 이름은 공백 없이 한 단어로 입력하세요.')
    return updated


# Codex 모델 ---------------------------------------------------------------

def models_path(home=None):
    """CODEX_HOME wins, as it does for the CLI itself; otherwise ~/.codex."""
    base = home or os.environ.get('CODEX_HOME') or Path.home() / '.codex'
    return Path(base) / MODELS_CACHE


def model_choices(data):
    """[(slug, name, description)] from Codex's cache. Pure, so a shape change is testable.

    Only 'list' models are offered: the cache also carries hidden internal ones the
    account cannot select, and a radio button that always fails is worse than none.
    """
    models = (data or {}).get('models') if isinstance(data, dict) else None
    rows = []
    for entry in models or ():
        if not isinstance(entry, dict) or entry.get('visibility') != 'list':
            continue
        slug = str(entry.get('slug') or '').strip()
        if not slug or len(slug.split()) != 1:
            continue
        # The hint leads with the slug: it is what goes on the command line, and it is
        # the half a user can compare against a Codex error message. Codex's own blurb
        # follows when it has one — third-party data, like a mail subject.
        blurb = str(entry.get('description') or '').strip()
        rows.append((slug, str(entry.get('display_name') or slug),
                     f'{slug} · {blurb}' if blurb else slug))
    return rows


def read_models(path=None):
    """What the CLI last saw. [] when Codex has never run here — never an exception."""
    try:
        target = Path(path) if path else models_path()
        return model_choices(json.loads(target.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return []


def model_rows(models, current=''):
    """The radio rows: 기본값, one per model Codex offers, then 직접 입력.

    A model saved before it left the cache keeps a row of its own, so opening 설정
    never silently reselects something else.
    """
    rows = [DEFAULT_MODEL] + [(slug, name, hint) for slug, name, hint in models]
    saved = str(current or '').strip()
    if saved and not any(slug == saved for slug, _, _ in rows):
        rows.append((saved, saved, '지금 설정된 모델입니다. Codex 목록에는 없습니다.'))
    return rows + [CUSTOM_MODEL]


def model_value(choice, typed):
    """What the radio and the box together mean for config.json."""
    return str(typed or '').strip() if choice == CUSTOM else str(choice or '').strip()
