"""화면 카피는 해요체다 — 그리고 Codex에게 보내는 프롬프트는 그 규칙 밖이다.

토스 에러 메시지 시스템에서 가져온 규칙 하나다. 격식체(~합니다/~입니다)도, '여기를
눌러주세요' 같은 명령형 라벨도 product 카피에 쓰지 않는다. 이 앱이 그 규칙을 받을
이유는 포지션이 같기 때문이다 — 메일을 대신 읽어 주는 조수는 '은행에 다니는 유능한
친구'와 같은 자리에 선다.

규칙 밖에 있는 것이 세 가지이고, 셋 다 사람이 읽는 문장이 아니다:

* `services.py`의 프롬프트 — Codex가 읽는다. 여기서 '~하세요'는 공손함이 아니라
  지시이고, 어조를 바꾸면 모델에게 하는 말이 달라진다.
* docstring과 주석 — 개발자가 읽는다. `CLAUDE.md`의 Style 절이 이미 영어로 쓰라고
  말하지만, 한국어로 쓴 것도 화면에 나가지 않는다는 점에서는 같다.
* 개발자에게만 닿는 예외 메시지 — `core.py`의 `SQL 리터럴에 쓸 수 없는 값입니다`는
  프로그래밍 오류이지 사용자가 고칠 수 있는 상황이 아니다.
"""
import ast
import re
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / 'mail_assistant'

# 화면에 닿는 모듈. services.py는 프롬프트와 오류 문장이 한 파일에 같이 있어서
# 아래 PROMPTS로 한 번 더 걸러진다.
SCREENS = ('webui.py', 'app.py', 'updater.py', 'usage.py', 'settings.py', 'money.py',
           'worker.py', 'notify.py', 'hub.py', 'excel.py', 'update.py',
           '__main__.py', 'webmain.py', 'services.py')

# Codex가 읽는 상수. 이름으로 거르는 이유는 위치로 거를 수 없기 때문이다 — 프롬프트와
# 사용자 오류 문장이 같은 모듈 안에 몇 줄 간격으로 서 있다.
PROMPTS = {'CHAT_PROMPT', 'BRIEF_PROMPT', 'CLIP_NOTE', 'MONEY_RULES', 'ANALYSIS_RULES',
           'BATCH_PROMPT', 'TRANSLATE_PROMPT', 'DRAFT_PROMPT'}

# 개발자에게만 닿는 예외. 사용자가 읽는 화면에는 올라오지 않는다.
DEVELOPER_ONLY = {('core.py', 'SQL 리터럴에 쓸 수 없는 값입니다')}

FORMAL = re.compile(r'습니다|입니다')


def literals(path):
    """(줄 번호, 문자열) — docstring과 프롬프트 상수를 뺀 나머지 전부."""
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    skip = set()

    def mark(node):
        first = node.body[0] if getattr(node, 'body', None) else None
        if isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant) \
                and isinstance(first.value.value, str):
            skip.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mark(node)
        # 프롬프트 상수는 통째로 건너뛴다.
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in PROMPTS for t in node.targets):
            skip.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.lineno not in skip:
            found.append((node.lineno, node.value))
    return found


class FormalToneTests(unittest.TestCase):
    """화면 문장에 격식체가 남아 있으면 그 화면만 다른 사람이 쓴 것처럼 읽힌다."""

    def test_no_screen_string_is_written_in_the_formal_style(self):
        stray = []
        for name in SCREENS:
            for line, value in literals(PACKAGE / name):
                for sentence in value.splitlines():
                    if not FORMAL.search(sentence):
                        continue
                    if any(name == where and mark in sentence
                           for where, mark in DEVELOPER_ONLY):
                        continue
                    stray.append(f'{name}:{line}  {sentence.strip()[:70]}')
        self.assertEqual(stray, [], '해요체로 고칠 문장:\n' + '\n'.join(stray))

    def test_the_sweep_actually_reads_the_screens(self):
        """걸러내기가 너무 세면 아무것도 보지 않고 통과한다."""
        seen = sum(len(literals(PACKAGE / name)) for name in SCREENS)
        self.assertGreater(seen, 500, seen)

    def test_the_prompts_are_left_alone(self):
        """Codex에게 하는 말은 바뀌지 않았다 — 어조가 아니라 지시이기 때문이다."""
        source = (PACKAGE / 'services.py').read_text(encoding='utf-8')
        self.assertIn('도구를 사용하지 마세요', source)
        self.assertIn('독립적으로 분석하세요', source)
        self.assertRegex(source, r'CHAT_PROMPT = """메일 업무를 돕는 상담 기능입니다')


class DirectiveLabelTests(unittest.TestCase):
    """'~하세요'는 '~해 주세요'로. 버튼과 안내는 명령이 아니라 부탁이다.

    토스의 Don't가 금지하는 것은 디렉티브 *라벨*이고, 부탁형(~해 주세요)은 에러 카피에
    정상적으로 쓰인다. 그래서 막는 것은 맨 명령형뿐이다.
    """

    ALLOWED = ('주세요', '마세요', '보세요')

    def test_screens_ask_rather_than_order(self):
        stray = []
        for name in SCREENS:
            if name == 'services.py':      # 프롬프트의 '~하세요'는 지시다
                continue
            for line, value in literals(PACKAGE / name):
                for sentence in value.splitlines():
                    for hit in re.findall(r'[가-힣]{2,}세요', sentence):
                        if not hit.endswith(self.ALLOWED):
                            stray.append(f'{name}:{line}  {hit}  —  {sentence.strip()[:60]}')
        self.assertEqual(stray, [], '부탁형으로 고칠 라벨:\n' + '\n'.join(stray))


if __name__ == '__main__':
    unittest.main()
