"""The Codex 사용량 meter: every line the chip and the 실행 card draw, with no Codex."""
import time
import unittest
from datetime import datetime
from pathlib import Path

from mail_assistant.core import KST
from mail_assistant.usage import (CALM, FULL, READ_SECONDS, STALE_SECONDS, WARN, Meter,
                                  as_percent, clock_text, level_of, plan_text, read_error,
                                  snapshot, taken_text, view, window_line, window_name,
                                  window_of, worst)

# What `account/rateLimits/read` answers, cut down to the keys this reads.
PAYLOAD = {'ordinaryUsageAllowed': True,
           'rateLimits': {'primary': {'usedPercent': 32, 'windowDurationMins': 300,
                                      'resetsAt': 1789449399},
                          'secondary': {'usedPercent': 20, 'windowDurationMins': 10080,
                                        'resetsAt': 1789948134},
                          'planType': 'plus', 'rateLimitReachedType': None}}


def moment(text):
    """A Korean-local wall clock as an epoch, so the reset lines are readable here."""
    return datetime.fromisoformat(text).replace(tzinfo=KST).timestamp()


class ShapeTests(unittest.TestCase):
    def test_the_payload_becomes_the_six_numbers_a_screen_shows(self):
        snap = snapshot(PAYLOAD, at=1789442000)
        self.assertEqual(snap['primary'], {'percent': 32, 'minutes': 300,
                                           'resets': 1789449399})
        self.assertEqual(snap['secondary']['percent'], 20)
        self.assertEqual((snap['plan'], snap['blocked']), ('plus', False))

    def test_anything_that_is_not_a_rate_limit_payload_is_nothing_at_all(self):
        for data in (None, {}, 'text', {'rateLimits': None}, {'rateLimits': {}},
                     {'rateLimits': {'primary': None, 'secondary': None}}):
            with self.subTest(data=data):
                self.assertEqual(snapshot(data), {})

    def test_a_window_with_no_percentage_is_absent_rather_than_zero(self):
        self.assertIsNone(window_of({'windowDurationMins': 300}))
        self.assertIsNone(window_of(None))
        self.assertEqual(window_of({'usedPercent': 0})['percent'], 0)

    def test_a_percentage_is_clamped_and_rounded_to_the_one_line_it_has(self):
        self.assertEqual([as_percent(v) for v in (12.4, 12.6, -3, 140, None, 'x')],
                         [12, 13, 0, 100, 0, 0])

    def test_being_out_is_the_backends_own_answer_and_never_read_off_100_percent(self):
        full = {'rateLimits': {'primary': {'usedPercent': 100}, 'planType': 'plus'}}
        self.assertFalse(snapshot(full)['blocked'])
        said = {'rateLimits': {'primary': {'usedPercent': 100},
                               'rateLimitReachedType': 'rate_limit_reached'}}
        self.assertTrue(snapshot(said)['blocked'])
        refused = {'ordinaryUsageAllowed': False,
                   'rateLimits': {'primary': {'usedPercent': 40}}}
        self.assertTrue(snapshot(refused)['blocked'])

    def test_the_window_that_bites_first_is_the_one_the_chip_shows(self):
        snap = snapshot({'rateLimits': {'primary': {'usedPercent': 12},
                                        'secondary': {'usedPercent': 95}}})
        self.assertEqual(worst(snap)['percent'], 95)
        self.assertIsNone(worst({}))


class SentenceTests(unittest.TestCase):
    def test_a_window_is_named_in_the_words_a_reader_uses(self):
        self.assertEqual([window_name(m) for m in (300, 10080, 1440, 2880, 45, 0)],
                         ['5시간 한도', '주간 한도', '하루 한도', '2일 한도', '45분 한도',
                          '사용 한도'])

    def test_a_reset_today_is_a_clock_and_any_other_day_carries_its_date(self):
        now = moment('2026-09-15T09:00:00')
        self.assertEqual(clock_text(moment('2026-09-15T14:16:00'), now), '14:16')
        self.assertEqual(clock_text(moment('2026-09-21T08:48:00'), now), '9월 21일 08:48')

    def test_a_reset_nobody_gave_is_left_out_rather_than_guessed(self):
        self.assertEqual(clock_text(0), '')
        self.assertEqual(clock_text(None), '')
        self.assertEqual(window_line('5시간 한도', {'percent': 32, 'resets': 0}),
                         '5시간 한도 32% 사용')
        self.assertEqual(window_line('5시간 한도', None), '')

    def test_the_korean_date_is_built_by_hand_and_never_by_strftime(self):
        # Windows encodes a format string with the locale codec, so a Korean one is a
        # UnicodeEncodeError on every PC that is not set to Korean.
        import re

        import mail_assistant.usage as module
        source = Path(module.__file__).read_text(encoding='utf-8')
        patterns = re.findall(r"""strftime\((['"])(.*?)\1\)""", source)
        self.assertTrue(patterns)
        for _, pattern in patterns:
            self.assertTrue(pattern.isascii(), pattern)

    def test_the_plan_is_the_word_on_the_account_and_silence_when_unknown(self):
        self.assertEqual(plan_text('plus'), 'ChatGPT Plus 요금제')
        self.assertEqual(plan_text('PRO'), 'ChatGPT Pro 요금제')
        self.assertEqual(plan_text('unknown'), '')
        self.assertEqual(plan_text(''), '')

    def test_a_reading_says_how_old_it_is_only_once_that_matters(self):
        now = time.time()
        self.assertEqual(taken_text(now - 60, now), '')
        self.assertIn('기준', taken_text(now - STALE_SECONDS - 60, now))

    def test_a_failure_keeps_its_type_where_a_reader_can_repeat_it(self):
        self.assertIn('OSError', read_error(OSError('끊겼습니다')))
        self.assertIn('응답이 없어요', read_error(TimeoutError()))


class LevelTests(unittest.TestCase):
    def test_a_quota_that_is_fine_is_not_lit_up(self):
        self.assertEqual(level_of(0), CALM)
        self.assertEqual(level_of(69), CALM)
        self.assertEqual(level_of(70), WARN)
        self.assertEqual(level_of(90), FULL)
        self.assertEqual(level_of(3, blocked=True), FULL)


class ViewTests(unittest.TestCase):
    def test_a_codex_nobody_has_managed_to_ask_draws_nothing(self):
        found = view({})
        self.assertFalse(found['known'])
        self.assertEqual(found['text'], '')

    def test_a_failed_read_with_no_reading_behind_it_still_says_what_happened(self):
        self.assertEqual(view({}, '확인하지 못했습니다')['lines'], ['확인하지 못했습니다'])

    def test_the_chip_reads_the_worse_of_the_two_windows(self):
        found = view(snapshot(PAYLOAD), now=moment('2026-09-15T09:00:00'))
        self.assertEqual(found['text'], 'Codex 사용량 32%')
        self.assertEqual(found['level'], CALM)
        self.assertEqual(found['lines'][0], '5시간 한도 32% 사용 · 14:16 초기화')
        self.assertIn('주간 한도 20% 사용', found['lines'][1])
        self.assertIn('Plus', found['lines'][-1])

    def test_being_out_says_so_and_says_when_it_comes_back(self):
        payload = {'ordinaryUsageAllowed': False,
                   'rateLimits': {'primary': {'usedPercent': 100, 'windowDurationMins': 300,
                                              'resetsAt': moment('2026-09-15T14:16:00')},
                                  'rateLimitReachedType': 'rate_limit_reached'}}
        found = view(snapshot(payload), now=moment('2026-09-15T09:00:00'))
        self.assertEqual(found['text'], 'Codex 한도 도달')
        self.assertEqual(found['level'], FULL)
        self.assertIn('14:16에 다시 쓸 수 있어요.', found['lines'])

    def test_a_high_but_open_quota_warns_without_claiming_it_is_spent(self):
        found = view(snapshot({'rateLimits': {'primary': {'usedPercent': 82}}}))
        self.assertEqual(found['text'], 'Codex 사용량 82%')
        self.assertEqual(found['level'], WARN)


class MeterTests(unittest.TestCase):
    def test_a_meter_with_nothing_to_ask_draws_nothing_and_never_raises(self):
        meter = Meter(None)
        self.assertIsNone(meter.look())
        self.assertIsNone(meter.watch())
        self.assertFalse(meter.view()['known'])

    def test_the_beat_asks_once_and_then_answers_out_of_what_it_has(self):
        calls = []

        def read():
            calls.append(1)
            return PAYLOAD

        meter = Meter(read, interval=READ_SECONDS)
        self.assertTrue(meter.watch())
        self.assertIsNone(meter.watch())         # still fresh: no second process
        self.assertEqual(len(calls), 1)
        self.assertEqual(meter.view()['percent'], 32)

    def test_a_press_is_answered_by_asking_and_not_out_of_the_cache(self):
        calls = []
        meter = Meter(lambda: calls.append(1) or PAYLOAD)
        meter.look()
        meter.look()
        self.assertEqual(len(calls), 2)

    def test_a_failure_keeps_the_last_reading_and_says_why_beside_it(self):
        state = {'fail': False}

        def read():
            if state['fail']:
                raise RuntimeError('Codex가 종료했습니다')
            return PAYLOAD

        meter = Meter(read)
        meter.look()
        state['fail'] = True
        self.assertIsNone(meter.look())
        found = meter.view()
        self.assertTrue(found['known'])          # the older number is still true of then
        self.assertEqual(found['percent'], 32)
        self.assertIn('Codex가 종료했습니다', found['lines'][-1])

    def test_a_failure_waits_out_the_same_beat_rather_than_retrying_every_tick(self):
        calls = []

        def read():
            calls.append(1)
            raise RuntimeError('안 됩니다')

        meter = Meter(read)
        self.assertIsNone(meter.watch())
        self.assertIsNone(meter.watch())
        self.assertEqual(len(calls), 1)

    def test_a_read_already_running_is_not_started_a_second_time(self):
        meter = Meter(lambda: PAYLOAD)
        meter.busy = True
        self.assertFalse(meter.due())
        self.assertIsNone(meter.look())
