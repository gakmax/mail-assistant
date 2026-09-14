"""The hub: one worker owned by the process, one log that outlives it.

`run` is injected, so none of this needs Windows, a window or a browser.
"""
import tempfile
import threading
import unittest
from pathlib import Path

from mail_assistant.core import LogStore, Store
from mail_assistant.hub import KEEP_LINES, Hub, line_text
from mail_assistant.services import CodexBusy, codex_slot

CONFIG = {'host': 'pop3s.hiworks.com', 'port': 995, 'email': 'me@corp.example', 'interval': 60}


def quiet(*args, **kwargs):
    """Swallow the report() calls so a test never posts anywhere."""


class Recorder:
    """A stand-in worker: records what it was handed, then waits to be stopped."""

    def __init__(self, messages=(), block=True):
        self.messages = list(messages)
        self.block = block
        self.calls = []
        self.entered = threading.Event()

    def __call__(self, config, directory, stop, notify, wake=None):
        self.calls.append((config, directory, stop, wake))
        self.entered.set()
        for message in self.messages:
            notify(message)
        if self.block:
            stop.wait(5)


class HubWorkerTests(unittest.TestCase):
    def hub(self, folder, run, **kwargs):
        return Hub(Path(folder), dict(CONFIG), run, report=quiet, **kwargs)

    def test_start_hands_the_worker_its_events_and_reports_running(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Recorder()
            hub = self.hub(folder, worker)
            self.assertFalse(hub.running())
            self.assertTrue(hub.start())
            self.assertTrue(worker.entered.wait(5))
            self.assertTrue(hub.running())
            config, directory, stop, wake = worker.calls[0]
            self.assertEqual(config['email'], CONFIG['email'])
            self.assertEqual(directory, Path(folder))
            self.assertIs(stop, hub.stopping)
            self.assertIs(wake, hub.waking)
            hub.halt()
            hub.thread.join(5)
            hub.close()

    def test_a_second_start_is_refused_so_two_workers_cannot_run(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Recorder()
            hub = self.hub(folder, worker)
            self.assertTrue(hub.start())
            self.assertTrue(worker.entered.wait(5))
            self.assertFalse(hub.start())        # already running
            self.assertEqual(len(worker.calls), 1)
            hub.halt()
            hub.thread.join(5)
            hub.close()

    def test_start_takes_the_settings_it_is_given(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Recorder(block=False)
            hub = self.hub(folder, worker)
            hub.start(dict(CONFIG, interval=900))
            self.assertTrue(worker.entered.wait(5))
            self.assertEqual(worker.calls[0][0]['interval'], 900)
            hub.close()

    def test_halt_and_wake_do_nothing_while_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = self.hub(folder, Recorder())
            self.assertFalse(hub.halt())
            self.assertFalse(hub.wake())
            self.assertFalse(hub.waking.is_set())
            hub.close()

    def test_halt_also_wakes_so_a_sleeping_cycle_stops_now(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Recorder()
            hub = self.hub(folder, worker)
            hub.start()
            self.assertTrue(worker.entered.wait(5))
            self.assertTrue(hub.halt())
            self.assertTrue(hub.stopping.is_set())
            self.assertTrue(hub.waking.is_set())
            hub.thread.join(5)
            hub.close()

    def test_a_worker_that_raises_is_reported_and_not_left_running(self):
        with tempfile.TemporaryDirectory() as folder:
            def explode(*args, **kwargs):
                raise OSError('데이터 폴더 접근 불가')
            seen = []
            hub = Hub(Path(folder), dict(CONFIG), explode, report=lambda *a: seen.append(a))
            watcher = hub.subscribe()
            hub.start()
            # Wait for the sentinel, not for running(): work() clears the thread first,
            # releases its connection, and only then sends None. Polling running() and
            # draining what happened to be queued is a race, and CI's Windows runner
            # lost it.
            drained = []
            while True:
                value = watcher.get(timeout=10)
                drained.append(value)
                if value is None:
                    break
            self.assertFalse(hub.running())
            self.assertEqual(seen[0][0], '작업 스레드 중단')
            self.assertIn('실행 오류: 데이터 폴더 접근 권한과 설치 상태를 확인하세요.', drained)
            self.assertIsNone(drained[-1])       # the sentinel comes last
            # The worker logged from its own thread; that connection must be gone, or
            # Windows keeps mail.db locked for as long as the process lives.
            self.assertFalse((Path(folder) / 'mail.db-wal').exists())
            hub.close()

    def test_running_is_already_false_when_the_sentinel_arrives(self):
        """A screen that reacts to None must not be told the worker is still alive."""
        with tempfile.TemporaryDirectory() as folder:
            hub = self.hub(folder, Recorder(block=False))
            watcher = hub.subscribe()
            hub.start()
            value = watcher.get(timeout=5)
            while value is not None:
                value = watcher.get(timeout=5)
            self.assertFalse(hub.running())
            hub.close()


class HubWatcherTests(unittest.TestCase):
    def test_every_watcher_sees_every_message(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            first, second = hub.subscribe(), hub.subscribe()
            hub.log('새 메일 2건 수집')
            self.assertEqual(first.get(timeout=2), '새 메일 2건 수집')
            self.assertEqual(second.get(timeout=2), '새 메일 2건 수집')
            hub.unsubscribe(second)
            hub.log('엑셀 반영 완료')
            self.assertEqual(first.get(timeout=2), '엑셀 반영 완료')
            self.assertTrue(second.empty())
            hub.close()

    def test_publish_turns_a_report_dict_into_a_line(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            watcher = hub.subscribe()
            hub.publish({'title': '분석 실패', 'body': '한도를 확인하세요'})
            self.assertEqual(watcher.get(timeout=2), '한도를 확인하세요')
            hub.publish({'title': '제목만 있음'})
            self.assertEqual(watcher.get(timeout=2), '제목만 있음')
            hub.close()

    def test_the_sentinel_is_passed_through_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            watcher = hub.subscribe()
            hub.publish(None)
            self.assertIsNone(watcher.get(timeout=2))
            self.assertEqual(hub.recent(), [])   # not a log line
            hub.close()

    def test_state_reports_what_the_run_tab_shows(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            hub.log('시작했습니다.')
            state = hub.state()
            self.assertFalse(state['running'])
            self.assertEqual(state['message'], '시작했습니다.')
            hub.close()


class HubRevisionTests(unittest.TestCase):
    """The one thing a screen can tell another screen: something you show has changed."""

    def test_a_touch_moves_the_number_every_time(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            self.assertEqual(hub.revision, 0)
            self.assertEqual(hub.touch(), 1)
            self.assertEqual(hub.touch(), 2)
            self.assertEqual(hub.revision, 2)
            hub.close()

    def test_nothing_happening_leaves_it_where_it_was(self):
        """A screen repaints on this, so a log line must not count as a change."""
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            hub.log('새 메일 0건 수집')
            hub.publish(None)
            self.assertEqual(hub.revision, 0)
            hub.close()


class HubLogTests(unittest.TestCase):
    def test_the_log_outlives_the_hub(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            first.log('어제 있었던 일')
            first.close()
            second = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            self.assertEqual([text for _, text in second.recent()], ['어제 있었던 일'])
            second.close()

    def test_recent_is_oldest_first_and_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            for index in range(5):
                hub.log(f'{index}번째')
            self.assertEqual([text for _, text in hub.recent()],
                             ['0번째', '1번째', '2번째', '3번째', '4번째'])
            self.assertEqual([text for _, text in hub.recent(2)], ['3번째', '4번째'])
            hub.close()

    def test_a_log_that_cannot_be_written_is_reported_not_raised(self):
        """The worker must not die because the log table is unreachable."""
        with tempfile.TemporaryDirectory() as folder:
            seen = []
            hub = Hub(Path(folder) / 'missing', dict(CONFIG), Recorder(),
                      report=lambda *a: seen.append(a))
            watcher = hub.subscribe()
            hub.log('갈 곳 없는 줄')
            self.assertEqual(seen[0][0], '실행 기록 저장 실패')
            self.assertEqual(watcher.get(timeout=2), '갈 곳 없는 줄')   # still shown live

    def test_line_text_uses_korean_local_time(self):
        self.assertEqual(line_text('2026-09-11T01:02:03+00:00', '수집 완료'),
                         '09-11 10:02:03  수집 완료')
        self.assertEqual(line_text('', '시각 없음'), '  시각 없음')

    def test_the_window_and_the_worker_share_one_database(self):
        """Store creates the log table too, so whoever opens the file first is fine."""
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            store.db.close()
            hub = Hub(Path(folder), dict(CONFIG), Recorder(), report=quiet)
            hub.log('한 파일')
            self.assertEqual(len(hub.recent()), 1)
            hub.close()


class LogStoreTests(unittest.TestCase):
    def test_trim_keeps_the_newest(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LogStore(Path(folder) / 'mail.db')
            for index in range(10):
                store.add(f'{index}')
            store.trim(3)
            self.assertEqual([text for _, text in store.recent(10)], ['7', '8', '9'])
            store.close()

    def test_trim_on_a_short_table_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LogStore(Path(folder) / 'mail.db')
            store.add('하나')
            store.trim(KEEP_LINES)
            self.assertEqual(len(store.recent(10)), 1)
            store.close()


class CodexSlotTests(unittest.TestCase):
    def test_only_one_holder_at_a_time(self):
        with codex_slot():
            with self.assertRaises(CodexBusy):
                with codex_slot(timeout=0.05):
                    self.fail('두 번째가 슬롯을 얻으면 안 됩니다')

    def test_the_slot_is_released_even_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with codex_slot(timeout=1):
                raise ValueError('분석 실패')
        with codex_slot(timeout=0.05):     # free again
            pass

    def test_a_waiter_gets_the_slot_when_the_holder_lets_go(self):
        held = threading.Event()
        done = threading.Event()

        def waiter():
            with codex_slot(timeout=5):
                done.set()

        with codex_slot():
            thread = threading.Thread(target=waiter, daemon=True)
            thread.start()
            held.set()
            self.assertFalse(done.wait(0.2))    # blocked while we hold it
        self.assertTrue(done.wait(5))
        thread.join(5)


if __name__ == '__main__':
    unittest.main()
