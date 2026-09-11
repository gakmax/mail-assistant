"""Update client tests. Pure logic plus faked network — no Windows, no sockets."""
import collections
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from mail_assistant import update


PAYLOAD = b'setup-bytes' * 4096
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def manifest(version='9.9.9', **overrides):
    data = {
        'schema': 1,
        'version': version,
        'notes': '엑셀 반영 오류를 고쳤습니다.',
        'installer': {
            'name': f'MailAssistant-Setup-{version}.exe',
            'url': f'https://github.com/x/y/releases/download/v{version}/MailAssistant-Setup-{version}.exe',
            'size': len(PAYLOAD),
            'sha256': SHA,
        },
    }
    data.update(overrides)
    return data


class FakeResponse:
    """Minimal urlopen() stand-in: context manager, read(n), getheader()."""

    def __init__(self, body, headers=None):
        self.body, self.headers, self.offset = body, headers or {}, 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=None):
        end = len(self.body) if size is None else min(len(self.body), self.offset + size)
        chunk, self.offset = self.body[self.offset:end], end
        return chunk

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class VersionTests(unittest.TestCase):
    def test_parse_accepts_tag_and_plain(self):
        for text, expected in (('v1.2.0', (1, 2, 0)), ('1.2.0', (1, 2, 0)),
                               ('V1.2', (1, 2)), ('1.2.0.3', (1, 2, 0, 3))):
            with self.subTest(text=text):
                self.assertEqual(update.parse_version(text), expected)

    def test_parse_rejects_anything_else(self):
        for text in ('1.2.0-rc1', 'latest', '', None, '1..2', '1.2.x', '1.2.3.4.5'):
            with self.subTest(text=text):
                self.assertIsNone(update.parse_version(text))

    def test_newer_pads_to_equal_length(self):
        self.assertFalse(update.newer((1, 2), (1, 2, 0)))
        self.assertTrue(update.newer((1, 2, 1), (1, 2)))

    def test_newer_compares_numerically_not_as_text(self):
        """'1.10.0' < '1.9.0' as strings; the whole point of using tuples."""
        self.assertTrue(update.newer((1, 10, 0), (1, 9, 0)))
        self.assertFalse(update.newer((1, 9, 0), (1, 10, 0)))

    def test_newer_is_false_when_either_side_is_unknown(self):
        self.assertFalse(update.newer(None, (1, 0)))
        self.assertFalse(update.newer((1, 0), None))

    def test_shipped_version_is_three_numeric_parts(self):
        """The release tag guard and the Windows version resource both need this."""
        from mail_assistant import __version__
        self.assertRegex(__version__, r'^\d+\.\d+\.\d+$')


class FormatTests(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(update.describe(24117248), '23.0MB')
        self.assertEqual(update.describe(512), '512B')
        self.assertEqual(update.describe(0), '0B')

    def test_summarize_keeps_short_notes_verbatim(self):
        self.assertEqual(update.summarize('첫 줄\n둘째 줄'), '첫 줄\n둘째 줄')

    def test_summarize_marks_what_it_cut(self):
        trimmed = update.summarize('\n'.join(str(i) for i in range(40)))
        self.assertTrue(trimmed.endswith('…'))
        self.assertEqual(len(trimmed.split('\n')), 13)

    def test_summarize_explains_an_empty_body(self):
        self.assertEqual(update.summarize(''), '변경 내용이 제공되지 않았습니다.')


class OfferTests(unittest.TestCase):
    def test_accepts_a_newer_release(self):
        offer = update.offer_from(manifest('2.0.0'), (1, 0, 0))
        self.assertEqual(offer['version'], '2.0.0')
        self.assertEqual(offer['sha256'], SHA)
        self.assertEqual(offer['size'], len(PAYLOAD))
        self.assertEqual(offer['silent'], update.SILENT)

    def test_ignores_same_or_older(self):
        self.assertIsNone(update.offer_from(manifest('1.0.0'), (1, 0, 0)))
        self.assertIsNone(update.offer_from(manifest('0.9.0'), (1, 0, 0)))

    def test_honours_a_skipped_version(self):
        self.assertIsNone(update.offer_from(manifest('2.0.0'), (1, 0, 0), skip='2.0.0'))
        self.assertIsNotNone(update.offer_from(manifest('2.1.0'), (1, 0, 0), skip='2.0.0'))

    def test_manifest_may_override_the_installer_flags(self):
        data = manifest('2.0.0', install={'silent_args': ['/VERYSILENT', '/NORESTART']})
        self.assertEqual(update.offer_from(data, (1, 0, 0))['silent'],
                         ('/VERYSILENT', '/NORESTART'))

    def test_rejects_a_malformed_manifest(self):
        broken = {
            'not a dict': [],
            'bad version': manifest('nightly'),
            'no installer': {'version': '2.0.0'},
            'http url': manifest('2.0.0', installer=dict(manifest('2.0.0')['installer'],
                                                         url='http://evil/setup.exe')),
            'short hash': manifest('2.0.0', installer=dict(manifest('2.0.0')['installer'],
                                                           sha256='abc')),
        }
        for name, data in broken.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    update.offer_from(data, (1, 0, 0))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        for name in ('MAIL_ASSISTANT_UPDATE', 'MAIL_ASSISTANT_UPDATE_REPO'):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def test_defaults(self):
        options = update.settings({})
        self.assertTrue(options['enabled'])
        self.assertEqual(options['repo'], update.DEFAULT_REPO)

    def test_config_can_disable(self):
        self.assertFalse(update.settings({'update': False})['enabled'])

    def test_environment_overrides_config(self):
        os.environ['MAIL_ASSISTANT_UPDATE'] = '0'
        self.assertFalse(update.settings({})['enabled'])
        os.environ['MAIL_ASSISTANT_UPDATE'] = 'force'
        self.assertTrue(update.settings({'update': False})['forced'])

    def test_repo_override(self):
        os.environ['MAIL_ASSISTANT_UPDATE_REPO'] = 'a/b'
        self.assertEqual(update.settings({'update_repo': 'c/d'})['repo'], 'a/b')
        os.environ.pop('MAIL_ASSISTANT_UPDATE_REPO')
        self.assertEqual(update.settings({'update_repo': 'c/d'})['repo'], 'c/d')


class RememberTests(unittest.TestCase):
    def test_preserves_keys_it_does_not_own(self):
        """The regression guard for settings-save wiping the webhook opt-out."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'webhook': '', 'email': 'a@b.c'}), encoding='utf-8')
            update.remember(path, {'update_skip': '1.2.0'})
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved, {'webhook': '', 'email': 'a@b.c', 'update_skip': '1.2.0'})

    def test_refuses_to_clobber_an_unreadable_file(self):
        """config.json holds the account and workbook path; never overwrite what we cannot read."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text('{ not json', encoding='utf-8')
            update.remember(path, {'update_checked': 1.0})
            self.assertEqual(path.read_text(encoding='utf-8'), '{ not json')

    def test_creates_a_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            update.remember(path, {'update_checked': 1.0})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'update_checked': 1.0})


class CheckTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop('MAIL_ASSISTANT_UPDATE', None)
        self.addCleanup(os.environ.pop, 'MAIL_ASSISTANT_UPDATE', None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config_path = Path(folder.name) / 'config.json'
        frozen = patch.object(sys, 'frozen', True, create=True)
        frozen.start()
        self.addCleanup(frozen.stop)
        opener = patch('mail_assistant.update.urllib.request.urlopen')
        self.urlopen = opener.start()
        self.addCleanup(opener.stop)
        reporter = patch('mail_assistant.update.report')
        self.report = reporter.start()
        self.addCleanup(reporter.stop)

    def respond(self, data):
        self.urlopen.return_value = FakeResponse(json.dumps(data).encode('utf-8'))

    def test_returns_an_offer_and_identifies_itself(self):
        self.respond(manifest('9.9.9'))
        offer = update.check({}, self.config_path)
        self.assertEqual(offer['version'], '9.9.9')
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.get_header('User-agent'), update.AGENT)
        self.assertIn('releases/latest/download/latest.json', request.full_url)

    def test_records_the_check_time(self):
        self.respond(manifest('9.9.9'))
        update.check({}, self.config_path)
        self.assertGreater(json.loads(self.config_path.read_text())['update_checked'], 0)

    def test_disabled_never_reaches_the_network(self):
        for config, env in (({'update': False}, None), ({}, '0'), ({}, 'false')):
            with self.subTest(config=config, env=env):
                self.urlopen.reset_mock()
                if env is None:
                    os.environ.pop('MAIL_ASSISTANT_UPDATE', None)
                else:
                    os.environ['MAIL_ASSISTANT_UPDATE'] = env
                self.assertIsNone(update.check(config, self.config_path))
                self.urlopen.assert_not_called()

    def test_a_source_checkout_does_not_check(self):
        with patch.object(sys, 'frozen', False):
            self.assertIsNone(update.check({}, self.config_path))
        self.urlopen.assert_not_called()

    def test_cache_suppresses_a_second_check(self):
        self.respond(manifest('9.9.9'))
        config = {'update_checked': time.time()}
        self.assertIsNone(update.check(config, self.config_path))
        self.urlopen.assert_not_called()
        self.respond(manifest('9.9.9'))
        self.assertIsNotNone(update.check(config, self.config_path, force=True))

    def test_offline_is_silent_and_reported(self):
        self.urlopen.side_effect = urllib.error.URLError('unreachable')
        self.assertIsNone(update.check({}, self.config_path))
        self.assertEqual(self.report.call_count, 1)

    def test_missing_release_is_not_reported(self):
        """404 is normal before the first release exists."""
        self.urlopen.side_effect = urllib.error.HTTPError('u', 404, 'Not Found', {}, None)
        self.assertIsNone(update.check({}, self.config_path))
        self.report.assert_not_called()

    def test_server_error_is_reported(self):
        self.urlopen.side_effect = urllib.error.HTTPError('u', 503, 'Unavailable', {}, None)
        self.assertIsNone(update.check({}, self.config_path))
        self.assertEqual(self.report.call_count, 1)

    def test_broken_manifest_is_reported_not_raised(self):
        for body in (b'not json', json.dumps(manifest('nightly')).encode()):
            with self.subTest(body=body[:20]):
                self.report.reset_mock()
                self.urlopen.return_value = FakeResponse(body)
                self.assertIsNone(update.check({}, self.config_path, force=True))
                self.assertEqual(self.report.call_count, 1)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name) / 'update' / '9.9.9'
        self.offer = update.offer_from(manifest('9.9.9'), (1, 0, 0))
        opener = patch('mail_assistant.update.urllib.request.urlopen')
        self.urlopen = opener.start()
        self.addCleanup(opener.stop)
        self.urlopen.return_value = FakeResponse(PAYLOAD, {'Content-Length': str(len(PAYLOAD))})

    def test_writes_and_verifies(self):
        progress = {}
        path = update.download(self.offer, self.work, progress)
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertFalse((self.work / 'setup.part').exists())
        self.assertEqual(progress['done'], len(PAYLOAD))
        record = json.loads((self.work / 'pending.json').read_text(encoding='utf-8'))
        self.assertEqual(record['version'], '9.9.9')

    def test_reuses_an_already_verified_file(self):
        update.download(self.offer, self.work)
        self.urlopen.reset_mock()
        update.download(self.offer, self.work)
        self.urlopen.assert_not_called()

    def test_hash_mismatch_leaves_nothing_behind(self):
        offer = dict(self.offer, sha256='0' * 64)
        with self.assertRaises(update.BadDigest):
            update.download(offer, self.work)
        self.assertFalse((self.work / 'setup.exe').exists())
        self.assertFalse((self.work / 'setup.part').exists())

    def test_cancel_removes_the_partial_file(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(update.Cancelled):
            update.download(self.offer, self.work, cancel=cancel)
        self.assertFalse((self.work / 'setup.part').exists())

    def test_refuses_without_headroom(self):
        usage = collections.namedtuple('usage', 'total used free')(0, 0, len(PAYLOAD))
        with patch('mail_assistant.update.shutil.disk_usage', return_value=usage):
            with self.assertRaises(update.NoSpace):
                update.download(self.offer, self.work)
        self.urlopen.assert_not_called()


class LaunchTests(unittest.TestCase):
    def test_passes_silent_flags_and_never_overrides_tasks(self):
        with patch('mail_assistant.update.subprocess.Popen') as popen:
            command = update.launch(Path('/tmp/setup.exe'), '/tmp/install.log')
        self.assertEqual(command[1:5], list(update.SILENT))
        self.assertIn('/LOG=/tmp/install.log', command)
        self.assertIn('/relaunch=1', command)
        self.assertFalse([arg for arg in command if 'TASKS' in arg.upper()])
        # DETACHED_PROCESS where it exists, 0 elsewhere -- the lazy lookup that
        # keeps this module importable off Windows.
        import subprocess
        self.assertEqual(popen.call_args.kwargs['creationflags'],
                         getattr(subprocess, 'DETACHED_PROCESS', 0))

    def test_autostart_mode(self):
        with patch('mail_assistant.update.subprocess.Popen'):
            command = update.launch(Path('/tmp/setup.exe'), '/tmp/x.log', autostart=True)
        self.assertIn('/relaunch=autostart', command)


class SweepTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        reporter = patch('mail_assistant.update.report')
        self.report = reporter.start()
        self.addCleanup(reporter.stop)

    def make(self, name, record=None, age=0):
        child = self.root / name
        child.mkdir(parents=True)
        if record is not None:
            (child / 'pending.json').write_text(json.dumps(record), encoding='utf-8')
        if age:
            os.utime(child, (time.time() - age, time.time() - age))
        return child

    def test_reports_an_install_that_did_not_take(self):
        self.make('9.9.9', {'version': '9.9.9', 'launched': time.time() - 3600})
        update.sweep(self.root)
        self.assertEqual(self.report.call_count, 1)
        self.assertFalse((self.root / '9.9.9').exists())

    def test_leaves_a_freshly_launched_install_alone(self):
        self.make('9.9.9', {'version': '9.9.9', 'launched': time.time()})
        update.sweep(self.root)
        self.report.assert_not_called()
        self.assertTrue((self.root / '9.9.9').exists())

    def test_clears_a_successful_install(self):
        self.make('0.0.1', {'version': '0.0.1', 'launched': time.time() - 3600})
        update.sweep(self.root)
        self.report.assert_not_called()
        self.assertFalse((self.root / '0.0.1').exists())

    def test_removes_week_old_orphans_only(self):
        self.make('old', age=update.STALE_SECONDS + 60)
        self.make('recent')
        update.sweep(self.root)
        self.assertFalse((self.root / 'old').exists())
        self.assertTrue((self.root / 'recent').exists())

    def test_missing_folder_is_fine(self):
        update.sweep(self.root / 'nope')


if __name__ == '__main__':
    unittest.main()
