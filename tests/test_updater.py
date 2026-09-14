"""The update state machine the native window draws, with no toolkit in sight."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_assistant import update
from mail_assistant.updater import (FAILED, IDLE, READY, WORKING, Updater, consequence,
                                    failure_text, offer_line, progress_text)

OFFER = {'version': '0.4.0', 'name': 'Setup-0.4.0.exe', 'url': 'https://example/x.exe',
         'sha256': 'a' * 64, 'size': 31_457_280, 'notes': '- 새 화면', 'silent': update.SILENT}


class TextTests(unittest.TestCase):
    """Every line the screen shows is built here, so the screen stays untested code."""

    def test_a_cancel_is_not_reported_as_a_breakage(self):
        text = failure_text(update.Cancelled())
        self.assertIn('취소', text)
        self.assertNotIn('손상', text)

    def test_a_bad_digest_says_the_install_was_stopped(self):
        self.assertIn('손상', failure_text(update.BadDigest('a' * 64, 'b' * 64)))

    def test_no_space_says_how_much_is_needed(self):
        self.assertIn(update.describe(500_000_000), failure_text(update.NoSpace(500_000_000)))

    def test_anything_else_keeps_the_type_and_the_reassurance(self):
        text = failure_text(OSError('연결이 끊겼습니다'))
        self.assertIn('OSError', text)
        self.assertIn('지금 버전은 그대로', text)

    def test_progress_reads_as_two_sizes(self):
        self.assertEqual(progress_text({'done': 1_048_576, 'total': 2_097_152}),
                         f'{update.describe(1_048_576)} / {update.describe(2_097_152)}')

    def test_an_unknown_size_counts_towards_nothing(self):
        self.assertEqual(progress_text({'done': 5, 'total': 0}), '')
        self.assertEqual(progress_text(None), '')

    def test_the_offer_line_names_the_version_you_have(self):
        self.assertIn('현재 0.3.0', offer_line(OFFER, '0.3.0'))
        self.assertIn(update.describe(OFFER['size']), offer_line(OFFER, '0.3.0'))

    def test_a_manifest_without_a_size_still_has_a_line(self):
        self.assertEqual(offer_line(dict(OFFER, size=0), '0.3.0'), '현재 0.3.0')
        self.assertEqual(offer_line(None, '0.3.0'), '')

    def test_a_running_collector_is_told_it_will_be_waited_for(self):
        self.assertIn('끝난 뒤', consequence(True))
        self.assertIn('다시 시작', consequence(False))


class UpdaterTests(unittest.TestCase):
    def make(self, folder):
        path = Path(folder) / 'config.json'
        path.write_text(json.dumps({'update_skip': ''}), encoding='utf-8')
        return Updater(Path(folder), {'update_skip': ''}, path)

    def test_nothing_offered_is_nothing_to_draw(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            with patch.object(update, 'check', return_value=None):
                self.assertIsNone(worker.look())
            self.assertFalse(worker.waiting())
            self.assertIsNone(worker.installer)

    def test_an_offer_is_held_until_something_is_done_about_it(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            with patch.object(update, 'check', return_value=OFFER):
                worker.look()
            self.assertTrue(worker.waiting())
            self.assertEqual(worker.state, IDLE)

    def test_a_finished_download_leaves_main_what_it_needs(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            worker.offer = OFFER
            with patch.object(update, 'download', return_value=Path(folder) / 'setup.exe'):
                self.assertTrue(worker.take(running=True))
            self.assertEqual(worker.state, READY)
            self.assertEqual(worker.installer, Path(folder) / 'setup.exe')
            self.assertTrue(worker.autostart)       # a collector was running: resume it
            self.assertFalse(worker.waiting())

    def test_a_stopped_collector_is_not_relaunched_with_autostart(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            worker.offer = OFFER
            with patch.object(update, 'download', return_value=Path(folder) / 'setup.exe'):
                worker.take(running=False)
            self.assertFalse(worker.autostart)

    def test_a_cancel_keeps_the_offer_so_it_can_be_tried_again(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            worker.offer = OFFER
            with patch.object(update, 'download', side_effect=update.Cancelled()):
                self.assertFalse(worker.take())
            self.assertEqual(worker.state, IDLE)
            self.assertTrue(worker.waiting())       # the 실행 button stays
            self.assertIn('취소', worker.message)
            self.assertIsNone(worker.installer)

    def test_a_real_failure_says_so_and_still_offers_a_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            worker.offer = OFFER
            with patch.object(update, 'download', side_effect=update.BadDigest('a' * 64, 'b' * 64)):
                worker.take()
            self.assertEqual(worker.state, FAILED)
            self.assertTrue(worker.waiting())
            self.assertIsNone(worker.installer)

    def test_a_download_already_running_is_not_started_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            worker.offer = OFFER
            worker.state = WORKING
            with patch.object(update, 'download') as pulled:
                self.assertFalse(worker.take())
            pulled.assert_not_called()

    def test_skipping_writes_the_version_and_drops_the_offer(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            worker.offer = OFFER
            self.assertEqual(worker.skip(), '0.4.0')
            self.assertIsNone(worker.offer)
            self.assertFalse(worker.waiting())
            self.assertEqual(worker.config['update_skip'], '0.4.0')
            saved = json.loads(worker.config_path.read_text(encoding='utf-8'))
            self.assertEqual(saved['update_skip'], '0.4.0')

    def test_skipping_nothing_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.make(folder).skip(), '')

    def test_stop_raises_the_flag_download_watches(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = self.make(folder)
            worker.stop()
            self.assertTrue(worker.cancel.is_set())


if __name__ == '__main__':
    unittest.main()
