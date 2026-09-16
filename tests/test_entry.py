"""The entry point's own decisions. main() needs Windows; these parts do not."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_assistant import __main__ as entry
from mail_assistant.settings import DEFAULTS

WORKBOOK = r'C:\Users\me\Desktop\메일 업무관리.xlsx'


class ConfigTests(unittest.TestCase):
    def test_no_file_yet_is_the_defaults_and_no_complaint(self):
        with tempfile.TemporaryDirectory() as folder:
            config, complaint = entry.read_config(Path(folder) / 'config.json', WORKBOOK)
            self.assertEqual(complaint, '')
            self.assertEqual(config['workbook'], WORKBOOK)
            self.assertEqual(config['host'], DEFAULTS['host'])

    def test_a_saved_file_wins_over_the_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'email': 'me@corp.example', 'workbook': 'X.xlsx'}),
                            encoding='utf-8')
            config, complaint = entry.read_config(path, WORKBOOK)
            self.assertEqual(complaint, '')
            self.assertEqual(config['email'], 'me@corp.example')
            self.assertEqual(config['workbook'], 'X.xlsx')

    def test_a_broken_file_still_opens_the_app_and_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text('{ not json', encoding='utf-8')
            with patch.object(entry, 'report') as told:
                config, complaint = entry.read_config(path, WORKBOOK)
            self.assertIn('설정', complaint)
            self.assertEqual(config['workbook'], WORKBOOK)     # still usable
            told.assert_called_once()


class NativeChoiceTests(unittest.TestCase):
    """Which window opens. The fallback is the whole point of asking."""

    def test_window_flag_takes_the_tkinter_window(self):
        with patch.object(sys, 'argv', ['MailAssistant.exe', '--window']):
            self.assertFalse(entry.native_ready())

    def test_no_pywebview_falls_back_instead_of_showing_nothing(self):
        with patch.object(sys, 'argv', ['MailAssistant.exe']), \
                patch.dict('sys.modules', {'webview': None}), \
                patch.object(entry, 'report') as told:
            self.assertFalse(entry.native_ready())
        told.assert_called_once()

    def test_pywebview_present_means_the_native_window(self):
        import types
        with patch.object(sys, 'argv', ['MailAssistant.exe']), \
                patch.dict('sys.modules', {'webview': types.ModuleType('webview')}):
            self.assertTrue(entry.native_ready())


class OutputTests(unittest.TestCase):
    """console=False can leave sys.stdout as None, and uvicorn writes to it."""

    def test_a_missing_stdout_is_given_a_file_to_write_to(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
                entry.route_output(Path(folder))
                stream = sys.stdout
                self.assertIsNotNone(stream)
                self.assertIs(sys.stderr, stream)
                print('한글도 쓸 수 있어야 한다')
                stream.flush()
            self.assertIn('한글도', (Path(folder) / 'window.log').read_text(encoding='utf-8'))
            stream.close()

    def test_the_log_is_one_launch_long_not_a_growing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'window.log'
            log.write_text('지난 실행의 내용', encoding='utf-8')
            with patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
                entry.route_output(Path(folder))
                stream = sys.stdout
            stream.close()
            self.assertNotIn('지난 실행', log.read_text(encoding='utf-8'))

    def test_a_console_build_keeps_the_console_it_has(self):
        with tempfile.TemporaryDirectory() as folder:
            mine = io.StringIO()
            with patch.object(sys, 'stdout', mine), patch.object(sys, 'stderr', mine):
                entry.route_output(Path(folder))
                self.assertIs(sys.stdout, mine)
            self.assertFalse((Path(folder) / 'window.log').exists())

    def test_an_unwritable_folder_does_not_stop_the_app_opening(self):
        with patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
            entry.route_output(Path('/nonexistent-folder-for-the-test'))
            stream = sys.stdout
            self.assertIsNotNone(stream)
            print('버려져도 된다')
        stream.close()


class SelftestSampleTests(unittest.TestCase):
    """selftest가 검증에 쓰는 예시 답이 스키마와 같이 자라는지.

    이 테스트가 있는 이유는 0.11.0 태그다. money와 order_no가 required가 된 뒤에도
    packaging/entry_tools.py의 fixture는 그대로였고, 진짜 jsonschema 4.x가 도는 곳이
    Windows 릴리즈 빌드뿐이라 태그를 밀고 나서야 'money is a required property'로
    터졌다. 스키마 dict를 읽는 것은 순수한 일이고, 여기서 하면 데스크톱에서 잡힌다.
    """

    def sample(self):
        """packaging/은 패키지가 아니라서 경로로 집어 온다."""
        import importlib.util
        path = Path(__file__).resolve().parent.parent / 'packaging' / 'entry_tools.py'
        spec = importlib.util.spec_from_file_location('entry_tools_for_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.SCHEMA_SAMPLE

    def test_the_sample_carries_every_field_the_schema_requires(self):
        from mail_assistant.services import schema
        required = set(schema()['required'])
        missing = sorted(required - set(self.sample()))
        self.assertEqual(missing, [], f'selftest 예시에 빠진 필드: {missing}')

    def test_the_sample_invents_nothing_the_schema_refuses(self):
        """additionalProperties가 False이므로 남는 키도 실패한다."""
        from mail_assistant.services import schema
        allowed = set(schema()['properties'])
        extra = sorted(set(self.sample()) - allowed)
        self.assertEqual(extra, [], f'스키마에 없는 필드: {extra}')


if __name__ == '__main__':
    unittest.main()
