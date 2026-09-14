"""The web 현황 screen's shaping, which needs no nicegui and so runs on every platform."""
import datetime
import socket
import tempfile
import types
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

from mail_assistant.core import FAILED, HANDLED, PROGRESS, SORTS, Store, account_key
from mail_assistant.dashboard import PRIORITIES
from mail_assistant.hub import Hub
from mail_assistant.overview import DUE_DAYS, due_window, failures, oldest_open, overview
from mail_assistant.webui import (CARD_TONES, DEFAULT_LIST, FONT_FILE, STATE_TONES, STATUS,
                                  THEME, TREND_LABELS,
                                  bar_option, bar_rows, card_icon, deadline_rows,
                                  detail_view, href, list_state, listing, new_token, open_port,
                                  release_store, run_summary, snapshot,
                                  soft_of, summary_line, tag_cell, tidy_body,
                                  calendar_events, card_target, countdown_text, run_view,
                                  FAILED_CARD, board, board_counts, card_hint, card_rows,
                                  chat_context, draft_view, label_step, stats_view,
                                  deadline_progress, drag_drop, drag_payload, drag_start,
                                  rich_text,
                                  trend_option, vendor_path,
                                  LIST_FIELDS, clicked_key, collect_text, header_cell, next_sort,
                                  WINDOW, WINDOW_FLOOR, WINDOW_MIN, window_size)

CONFIG = {'host': 'pop3s.hiworks.com', 'port': 995, 'email': 'me@corp.example'}
TODAY = datetime.date(2026, 9, 11)


def mail(subject='제목', sender='sender@example.com'):
    message = EmailMessage()
    message['From'] = sender
    message['Subject'] = subject
    message['Date'] = 'Fri, 11 Sep 2026 10:00:00 +0900'
    message.set_content('본문')
    return message.as_bytes()


@contextmanager
def workspace():
    """A temp folder that releases the cached connection before it is removed.

    tearDown is too late: it runs after TemporaryDirectory has already tried to
    delete mail.db, and on Windows an open handle makes that fail.
    """
    with tempfile.TemporaryDirectory() as folder:
        try:
            yield Path(folder)
        finally:
            release_store()


def result(priority='보통', deadline='', reply=False):
    return {'category': '업무 요청', 'summary': '요약', 'requests': '',
            'events': [{'title': '회신', 'start': '', 'deadline': deadline,
                        'evidence': 'e', 'needs_review': False}] if deadline else [],
            'priority': priority, 'priority_reason': '근거', 'next_action': '확인',
            'reply_needed': reply, 'reply_subject': 'Re: 제목' if reply else '',
            'reply_draft': '초안' if reply else ''}


def style_expression(template):
    """What sits inside the slot's :style="…" — the part that must survive the quotes."""
    head = template.split(':style="', 1)[1]
    return head.split('">', 1)[0]


class WindowSizeTests(unittest.TestCase):
    """The frame opens at WINDOW, except on a screen that cannot hold it."""

    def test_a_big_screen_gets_the_size_the_layout_was_drawn_for(self):
        self.assertEqual(window_size((1920, 1080)), WINDOW)
        self.assertEqual(window_size((3840, 2160)), WINDOW)

    def test_a_laptop_screen_keeps_the_window_inside_the_desktop(self):
        width, height = window_size((1366, 768))
        self.assertLess(width, 1366)
        self.assertLess(height, 768)

    def test_a_screen_that_cannot_be_asked_still_opens_at_the_full_size(self):
        for screen in (None, (), (0, 0), (0,)):
            with self.subTest(screen=screen):
                self.assertEqual(window_size(screen), WINDOW)

    def test_a_silly_screen_value_never_collapses_the_window(self):
        self.assertEqual(window_size((100, 100)), WINDOW_FLOOR)

    def test_the_minimum_is_smaller_than_what_the_window_opens_at(self):
        self.assertLess(WINDOW_MIN, WINDOW)


class ChartOptionTests(unittest.TestCase):
    """The option dicts are pure, so the charts are testable without a browser."""

    PAIRS = [('업무 요청', 3), ('견적·계약', 0), ('문의', 5)]

    def test_bars_read_top_down_in_the_order_given(self):
        option = bar_option(self.PAIRS)
        self.assertEqual(option['yAxis']['data'], ['문의', '견적·계약', '업무 요청'])
        self.assertEqual([item['value'] for item in option['series'][0]['data']], [5, 0, 3])

    def test_a_zero_still_draws_its_track(self):
        # Without showBackground a 0 is an invisible row, which is what the hand-drawn
        # bars used a grey track to avoid.
        self.assertTrue(bar_option(self.PAIRS)['series'][0]['showBackground'])

    def test_a_tone_map_colours_each_bar_by_name(self):
        option = bar_option([(name, 1) for name, _ in PRIORITIES], STATUS)
        colours = [item['itemStyle']['color'] for item in option['series'][0]['data']]
        self.assertEqual(colours, [STATUS[name] for name, _ in reversed(PRIORITIES)])

    def test_the_trend_draws_one_line_per_measure(self):
        rows = [(f'2026-09-{day:02d}', {'collected': day, 'analyzed': 0, 'exported': 1})
                for day in range(1, 8)]
        option = trend_option(rows)
        self.assertEqual([series['name'] for series in option['series']],
                         [label for _, label in TREND_LABELS])
        self.assertEqual(option['xAxis']['data'][0], '09-01')
        self.assertEqual(option['series'][0]['data'], list(range(1, 8)))

    def test_ninety_days_drop_their_symbols_and_thin_their_labels(self):
        rows = [(f'2026-{1 + day // 28:02d}-{1 + day % 28:02d}',
                 {'collected': 0, 'analyzed': 0, 'exported': 0}) for day in range(90)]
        option = trend_option(rows)
        self.assertFalse(option['series'][0]['showSymbol'])
        self.assertEqual(option['xAxis']['axisLabel']['interval'], label_step(90) - 1)


class TagCellTests(unittest.TestCase):
    def test_the_expression_never_closes_its_own_attribute(self):
        # The whole lookup lives inside :style="…"; one double quote in it and the
        # browser reads the rest of the expression as markup.
        self.assertNotIn('"', style_expression(tag_cell(STATE_TONES)))
        self.assertNotIn('"', style_expression(tag_cell(STATUS, failure='#c00000')))

    def test_every_tone_reaches_the_lookup(self):
        template = tag_cell(STATE_TONES)
        for name, colour in STATE_TONES.items():
            self.assertIn(f"'{name}':'color:{colour}", template)

    def test_a_counted_failure_is_matched_by_substring(self):
        # '3회 실패' carries its count, so it can never be a key in the table.
        self.assertIn("props.value.includes('실패')", tag_cell(STATE_TONES, failure='#c00000'))

    def test_an_empty_cell_draws_no_pill(self):
        self.assertIn('v-if="props.value"', tag_cell({}))

    def test_the_wash_comes_from_the_tone(self):
        self.assertEqual(soft_of('#123456'), '#1234561a')


class CardRowTests(unittest.TestCase):
    """The fifth card, and the line under a number."""

    def data(self, failed=0, oldest=None):
        return {'cards': {'미처리 메일': 3, '긴급·높음': 1, '7일 내 마감': 2, '검토 전 초안': 0},
                'failed': failed, 'oldest': oldest}

    def test_nothing_failed_leaves_the_four_fixed_cards(self):
        self.assertEqual([name for name, _ in card_rows(self.data())],
                         ['미처리 메일', '긴급·높음', '7일 내 마감', '검토 전 초안'])

    def test_a_failure_adds_its_own_card_at_the_end(self):
        self.assertEqual(card_rows(self.data(failed=2))[-1], (FAILED_CARD, 2))

    def test_the_failure_card_sends_you_to_the_failures(self):
        target = card_target(FAILED_CARD, 'tok')
        self.assertIn(f'state={FAILED}', target)
        self.assertTrue(target.startswith('/mail?t=tok'))

    def test_the_open_pile_says_how_long_as_well_as_how_many(self):
        self.assertEqual(card_hint('미처리 메일', self.data(oldest=12)), '가장 오래된 건 12일 경과')

    def test_nothing_open_means_no_line_at_all(self):
        self.assertEqual(card_hint('미처리 메일', self.data()), '')
        self.assertEqual(card_hint('긴급·높음', self.data(oldest=12)), '')

    def test_the_failure_card_says_the_worker_keeps_trying(self):
        self.assertIn('자동 재시도', card_hint(FAILED_CARD, self.data(failed=2)))


class CardIconTests(unittest.TestCase):
    def test_the_deadline_card_is_matched_by_its_suffix(self):
        # Its name carries the window, so 7일/14일/30일 are three different keys.
        for days in (7, 14, 30):
            self.assertEqual(card_icon(f'{days}일 내 마감'), 'schedule')

    def test_the_fixed_cards_keep_their_own_icons(self):
        self.assertEqual(card_icon('미처리 메일'), 'inbox')
        self.assertEqual(card_icon('긴급·높음'), 'priority_high')


class BarTests(unittest.TestCase):
    def test_bars_scale_to_the_largest_value(self):
        self.assertEqual(bar_rows([('a', 1), ('b', 4), ('c', 0)]),
                         [('a', 1, 25), ('b', 4, 100), ('c', 0, 0)])

    def test_all_zero_stays_at_zero_instead_of_dividing_by_zero(self):
        self.assertEqual(bar_rows([('a', 0), ('b', 0)]), [('a', 0, 0), ('b', 0, 0)])

    def test_no_pairs_is_no_rows(self):
        self.assertEqual(bar_rows([]), [])


class DeadlineRowTests(unittest.TestCase):
    def rows(self, folder):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        for uid, priority, deadline, reply in (('uid-1', '긴급', '2026-09-05', True),
                                               ('uid-2', '보통', '2026-09-13', False),
                                               ('uid-3', '높음', '2026-09-10', True)):
            ident = store.add(account, uid, mail())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result(priority, deadline, reply))
        return store

    def test_missed_deadlines_come_first_but_still_read_by_date(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            rows = deadline_rows(overview(store.page(account_key(CONFIG)), TODAY), TODAY)
            self.assertEqual([row['day'] for row in rows],
                             ['2026-09-05', '2026-09-10', '2026-09-13'])
            self.assertEqual([row['left'] for row in rows], ['6일 지남', '1일 지남', '2일 뒤'])
            self.assertEqual([row['missed'] for row in rows], [True, True, False])
            store.db.close()

    def test_every_row_carries_the_mail_it_came_from(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            rows = deadline_rows(overview(store.page(account_key(CONFIG)), TODAY), TODAY)
            known = {row['id'] for row in store.page(account_key(CONFIG))}
            self.assertTrue(all(row['mail'] in known for row in rows))
            store.db.close()

    def test_summary_mentions_misses_only_when_there_are_any(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            account = account_key(CONFIG)
            self.assertIn('지난 마감 2건', summary_line(overview(store.page(account), TODAY)))
            for row in store.page(account):
                store.set_handled(row['id'], HANDLED)
            self.assertNotIn('지난 마감', summary_line(overview(store.page(account), TODAY)))
            store.db.close()


class OpenPileTests(unittest.TestCase):
    """분석 실패 and 가장 오래된 건: the two numbers the 현황 cards had no way to say."""

    def rows(self, folder):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        ids = {}
        for uid in ('uid-1', 'uid-2', 'uid-3'):
            ids[uid] = store.add(account, uid, mail())
        for uid in ('uid-1', 'uid-2'):
            store.analyzed(ids[uid], {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result())
        return store, ids, account

    def aged(self, store, ident, stamp):
        store.db.execute('UPDATE mail SET received=? WHERE id=?', (stamp, ident))
        store.db.commit()

    def test_only_a_mail_that_tried_and_failed_counts_as_a_failure(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            rows = list(store.page(account))
            self.assertEqual(failures(rows), 0)       # uid-3 is 분석 대기, not 실패
            store.db.execute('UPDATE mail SET attempts=3 WHERE id=?', (ids['uid-3'],))
            store.db.commit()
            self.assertEqual(failures(list(store.page(account))), 1)
            store.db.close()

    def test_the_age_is_the_oldest_one_still_open(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            self.aged(store, ids['uid-1'], '2026-09-01T01:00:00+00:00')
            rows = list(store.page(account))
            self.assertEqual(oldest_open(rows, TODAY), 10)
            store.db.close()

    def test_closing_the_oldest_moves_the_age_to_the_next_one(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            self.aged(store, ids['uid-1'], '2026-09-01T01:00:00+00:00')
            self.aged(store, ids['uid-2'], '2026-09-08T01:00:00+00:00')
            store.set_handled(ids['uid-1'], HANDLED)
            rows = list(store.page(account))
            self.assertEqual(oldest_open(rows, TODAY, {ids['uid-1']}), 3)
            store.db.close()

    def test_an_empty_pile_has_no_age_and_so_no_line(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            handled = {ids['uid-1'], ids['uid-2']}
            self.assertIsNone(oldest_open(list(store.page(account)), TODAY, handled))
            store.db.close()

    def test_everything_arrived_today_says_nothing_rather_than_zero_days(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            for uid in ('uid-1', 'uid-2'):
                self.aged(store, ids[uid], '2026-09-11T01:00:00+00:00')
            data = overview(list(store.page(account)), TODAY)
            self.assertEqual(data['oldest'], 0)
            self.assertEqual(card_hint('미처리 메일', data), '')
            store.db.close()

    def test_a_clock_behind_the_server_does_not_report_negative_days(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            self.aged(store, ids['uid-1'], '2026-09-20T01:00:00+00:00')
            self.assertEqual(oldest_open(list(store.page(account)), TODAY), 0)
            store.db.close()


class DueWindowTests(unittest.TestCase):
    """The 마감 checklist keeps what is finished; it never loses what is still owed."""

    def rows(self, folder):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        ids = {}
        for uid, deadline in (('uid-1', '2026-09-05'), ('uid-2', '2026-09-13'),
                              ('uid-3', '2026-09-10')):
            ident = store.add(account, uid, mail())
            ids[uid] = ident
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result('보통', deadline))
        return store, ids, account

    def listed(self, store, account, within=DUE_DAYS):
        return deadline_rows(overview(store.page(account), TODAY, within=within), TODAY)

    def test_a_finished_deadline_keeps_its_place_and_stops_being_a_miss(self):
        with tempfile.TemporaryDirectory() as folder:
            store, ids, account = self.rows(folder)
            store.set_handled(ids['uid-3'], HANDLED)     # 2026-09-10, a day past
            rows = self.listed(store, account)
            self.assertEqual([row['day'] for row in rows],
                             ['2026-09-05', '2026-09-10', '2026-09-13'])
            done = next(row for row in rows if row['day'] == '2026-09-10')
            self.assertTrue(done['done'])
            self.assertFalse(done['missed'])
            store.db.close()

    def test_an_unfinished_miss_never_falls_off_however_old(self):
        with tempfile.TemporaryDirectory() as folder:
            store, ids, account = self.rows(folder)
            rows = self.listed(store, account, within=1)
            self.assertIn('2026-09-05', [row['day'] for row in rows])
            store.db.close()

    def test_a_finished_miss_older_than_the_window_drops_out(self):
        with tempfile.TemporaryDirectory() as folder:
            store, ids, account = self.rows(folder)
            store.set_handled(ids['uid-1'], HANDLED)     # 2026-09-05, six days past
            self.assertNotIn('2026-09-05',
                             [row['day'] for row in self.listed(store, account, within=1)])
            self.assertIn('2026-09-05',
                          [row['day'] for row in self.listed(store, account, within=30)])
            store.db.close()

    def test_the_card_still_counts_only_what_is_left(self):
        """The list keeps finished rows; the KPI beside it must not start counting them."""
        with tempfile.TemporaryDirectory() as folder:
            store, ids, account = self.rows(folder)
            before = overview(store.page(account), TODAY)['cards'][f'{DUE_DAYS}일 내 마감']
            store.set_handled(ids['uid-2'], HANDLED)     # 2026-09-13, inside the window
            after = overview(store.page(account), TODAY)['cards'][f'{DUE_DAYS}일 내 마감']
            self.assertEqual((before, after), (1, 0))
            store.db.close()

    def test_the_window_is_the_same_list_the_panel_draws(self):
        with tempfile.TemporaryDirectory() as folder:
            store, ids, account = self.rows(folder)
            data = overview(store.page(account), TODAY)
            self.assertEqual(data['due_window'],
                             due_window(data['events'], TODAY, handled=data['handled']))
            store.db.close()


class DeadlineProgressTests(unittest.TestCase):
    def rows(self, *done):
        return [{'done': flag} for flag in done]

    def test_it_counts_both_sides_and_rounds_the_percent(self):
        self.assertEqual(deadline_progress(self.rows(True, False, True)),
                         {'done': 2, 'left': 1, 'total': 3, 'ratio': 2 / 3, 'percent': 67})

    def test_an_empty_list_is_not_finished(self):
        self.assertEqual(deadline_progress([]),
                         {'done': 0, 'left': 0, 'total': 0, 'ratio': 0.0, 'percent': 0})

    def test_everything_done_fills_the_bar(self):
        self.assertEqual(deadline_progress(self.rows(True, True))['percent'], 100)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_reads_the_same_numbers_as_the_window(self):
        with workspace() as directory:
            store = Store(directory / 'mail.db')
            ident = store.add(account_key(CONFIG), 'uid-1', mail())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result('긴급', '2026-09-13', True))
            expected = overview(store.page(account_key(CONFIG)), TODAY)['cards']
            store.db.close()
            self.assertEqual(snapshot(directory, CONFIG, TODAY)['cards'], expected)

    def test_settings_without_an_account_read_as_an_empty_screen(self):
        with workspace() as folder:
            data = snapshot(folder, {}, TODAY)
            self.assertEqual(data['total'], 0)
            self.assertEqual(data['cards']['미처리 메일'], 0)


class RunStripTests(unittest.TestCase):
    """The web page reads the hub; it never owns the worker."""

    def hub(self, folder):
        return Hub(Path(folder), dict(CONFIG), lambda *a, **k: None,
                   report=lambda *a, **k: None)

    def test_a_stopped_hub_reads_as_stopped_with_no_stamps(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = self.hub(folder)
            summary = run_summary(hub, Path(folder), CONFIG)
            self.assertFalse(summary['running'])
            self.assertEqual(summary['label'], '중지됨')
            self.assertEqual(summary['stamps'], {'마지막 확인': '—', '마지막 반영': '—'})
            self.assertEqual(summary['lines'], [])
            hub.close()

    def test_the_strip_shows_the_stored_stamps_and_the_log_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            store = Store(directory / 'mail.db')
            account = account_key(CONFIG)
            store.set_meta('last_fetch:' + account, '2026-09-11T01:02:03+00:00')
            store.set_meta('last_export:' + account, '2026-09-11T01:05:00+00:00')
            store.db.close()
            hub = self.hub(folder)
            for index in range(3):
                hub.log(f'{index}번째 줄')
            summary = run_summary(hub, directory, CONFIG, limit=2)
            self.assertEqual(summary['stamps']['마지막 확인'], '09-11 10:02:03')
            self.assertEqual(summary['stamps']['마지막 반영'], '09-11 10:05:00')
            self.assertEqual(len(summary['lines']), 2)
            self.assertTrue(summary['lines'][-1].endswith('2번째 줄'))
            self.assertEqual(summary['message'], '2번째 줄')
            hub.close()

    def test_a_halting_worker_says_so_instead_of_running(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = self.hub(folder)
            hub.stopping.set()
            self.assertEqual(run_summary(hub, Path(folder), CONFIG)['label'], '중지 중…')
            hub.close()

    def test_settings_without_an_account_show_no_stamps_at_all(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = self.hub(folder)
            self.assertEqual(run_summary(hub, Path(folder), {})['stamps'], {})
            hub.close()


class ListStateTests(unittest.TestCase):
    def test_nothing_saved_is_the_default(self):
        self.assertEqual(list_state(None), DEFAULT_LIST)
        self.assertEqual(list_state({}), DEFAULT_LIST)

    def test_unknown_values_fall_back_instead_of_reaching_sql(self):
        state = list_state({'sort': 'DROP TABLE', 'state': '없는 상태', 'query': None})
        self.assertEqual(state['sort'], 'received')
        self.assertEqual(state['state'], '')
        self.assertEqual(state['query'], '')

    def test_paging_numbers_are_clamped(self):
        self.assertEqual(list_state({'page': -5, 'per': 1})['page'], 0)
        self.assertEqual(list_state({'per': 1})['per'], 10)
        self.assertEqual(list_state({'per': 9999})['per'], 200)
        self.assertEqual(list_state({'page': 'x', 'per': 'y'})['page'], 0)

    def test_saved_extras_are_dropped(self):
        self.assertNotIn('타인', list_state({'타인': '값'}))

    def test_links_always_carry_the_token(self):
        self.assertEqual(href('/mail', 'tok'), '/mail?t=tok')
        self.assertEqual(href('/mail', 'tok', id='abc'), '/mail?t=tok&id=abc')
        self.assertEqual(href('/mail', 'tok', id=None), '/mail?t=tok')


class HeaderSortTests(unittest.TestCase):
    """The column header is the sort control, so it has to name a sort SQL knows."""

    def test_every_column_can_be_sorted_by(self):
        for key, _ in LIST_FIELDS:
            self.assertIn(key, SORTS)

    def test_the_same_column_flips_and_a_new_one_opens_the_way_it_reads(self):
        self.assertEqual(next_sort('received', True, 'received'), ('received', False))
        self.assertEqual(next_sort('received', False, 'received'), ('received', True))
        # 제목 from ㄱ, 우선순위 most urgent first.
        self.assertEqual(next_sort('received', True, 'subject'), ('subject', False))
        self.assertEqual(next_sort('subject', False, 'priority'), ('priority', True))

    def test_a_column_sql_does_not_know_changes_nothing(self):
        self.assertEqual(next_sort('received', True, 'DROP TABLE'), ('received', True))

    def test_a_click_arrives_as_the_client_sent_it(self):
        self.assertEqual(clicked_key(['subject']), 'subject')
        self.assertEqual(clicked_key('subject'), 'subject')
        self.assertEqual(clicked_key([]), '')
        self.assertEqual(clicked_key(None), '')

    def test_the_header_never_closes_its_own_attribute(self):
        # Same rule as tag_cell: the emit sits inside a double-quoted Vue attribute.
        template = header_cell('received', 'received', True)
        inside = template.split('@click="', 1)[1].split('">', 1)[0]
        self.assertNotIn('"', inside)
        self.assertIn("$parent.$emit('sortby', 'received')", inside)

    def test_only_the_sorted_column_wears_the_arrow(self):
        live = header_cell('subject', 'subject', False)
        self.assertIn('ma-th is-live', live)
        self.assertIn('arrow_upward', live)
        self.assertIn('arrow_downward', header_cell('subject', 'subject', True))
        idle = header_cell('subject', 'received', True)
        self.assertNotIn('is-live', idle)
        self.assertIn('unfold_more', idle)

    def test_the_header_keeps_the_column_width(self):
        # q-table stops applying col.headerStyle once the cell is a slot of ours.
        self.assertIn(':style="props.col.headerStyle"', header_cell('received', 'received', True))


class CollectTextTests(unittest.TestCase):
    def test_a_stopped_collector_says_analysis_is_not_running(self):
        self.assertEqual(collect_text('새 메일 2건 수집', True), '새 메일 2건 수집')
        self.assertTrue(collect_text('새 메일 2건 수집', False).startswith('새 메일 2건 수집 · '))
        self.assertIn('분석', collect_text('새 메일 2건 수집', False))

    def test_a_silent_fetch_still_reports(self):
        self.assertEqual(collect_text('', True), '가져올 새 메일이 없습니다.')


class ListingTests(unittest.TestCase):
    def build(self, folder, count=25):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        ids = []
        for index in range(count):
            ident = store.add(account, f'uid-{index}', mail(f'메일 {index}'))
            ids.append(ident)
            if index % 2 == 0:
                store.analyzed(ident, {'sender': 'a@b.c', 'subject': f'메일 {index}',
                                       'attachments': []}, result('보통'))
        store.db.close()
        return ids

    def test_a_page_reports_its_slice_and_the_whole_total(self):
        with workspace() as folder:
            self.build(folder)
            data = listing(folder, CONFIG, list_state({'per': 10}))
            self.assertEqual(len(data['rows']), 10)
            self.assertEqual((data['total'], data['pages']), (25, 3))
            self.assertEqual((data['first'], data['last']), (1, 10))

    def test_the_last_page_is_short_not_empty(self):
        with workspace() as folder:
            self.build(folder)
            data = listing(folder, CONFIG, list_state({'per': 10, 'page': 2}))
            self.assertEqual((data['first'], data['last']), (21, 25))
            self.assertEqual(len(data['rows']), 5)

    def test_a_page_past_the_end_falls_back_to_the_last_one(self):
        """Narrowing the filter while on page 5 must not show an empty screen."""
        with workspace() as folder:
            self.build(folder)
            data = listing(folder, CONFIG, list_state({'per': 10, 'page': 9}))
            self.assertEqual(data['page'], 2)
            self.assertEqual(len(data['rows']), 5)

    def test_settings_without_an_account_list_nothing(self):
        with workspace() as folder:
            data = listing(folder, {}, list_state())
            self.assertEqual((data['rows'], data['total'], data['pages']), ([], 0, 0))

    def test_rows_are_the_same_shape_the_window_uses(self):
        with workspace() as folder:
            self.build(folder, count=1)
            row = listing(folder, CONFIG, list_state())['rows'][0]
            self.assertEqual(set(row), {'id', 'received', 'sender', 'subject', 'category',
                                        'priority', 'state', 'error'})


class DetailViewTests(unittest.TestCase):
    def test_an_analysed_mail_shows_its_verdict_and_its_body(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            ident = store.add(account_key(CONFIG), 'uid-1', mail('견적 요청', 'kim@buyer.example'))
            store.analyzed(ident, {'sender': 'kim@buyer.example', 'subject': '견적 요청',
                                   'body': '본문 전체', 'attachments': ['견적서.xlsx']},
                           result('긴급', '2026-09-20', reply=True))
            view = detail_view(store.detail(ident))
            self.assertEqual(view['subject'], '견적 요청')
            self.assertEqual(view['priority'], '긴급')
            self.assertEqual(view['attachments'], ['견적서.xlsx'])
            self.assertEqual(view['body'], '본문 전체')
            self.assertEqual(view['draft'], '초안')
            self.assertTrue(view['analysed'])
            self.assertEqual(len(view['events']), 1)
            store.db.close()

    def test_an_unanalysed_mail_still_renders_from_the_raw_message(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            ident = store.add(account_key(CONFIG), 'uid-1', mail('분석 전'))
            view = detail_view(store.detail(ident))
            self.assertFalse(view['analysed'])
            self.assertEqual(view['subject'], '분석 전')
            self.assertIn('본문', view['body'])          # parsed from raw
            self.assertEqual(view['state'], '분석 대기')
            self.assertEqual(view['draft'], '')
            store.db.close()

    def test_a_broken_parsed_blob_does_not_break_the_panel(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            ident = store.add(account_key(CONFIG), 'uid-1', mail('망가진 것'))
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '망가진 것', 'attachments': []},
                           result())
            store.db.execute("UPDATE mail SET parsed='{깨짐' WHERE id=?", (ident,))
            store.db.commit()
            view = detail_view(store.detail(ident))
            self.assertEqual(view['subject'], '망가진 것')   # from the column, not the blob
            self.assertEqual(view['body'], '')
            self.assertEqual(view['attachments'], [])
            store.db.close()

    def test_the_users_edit_wins_over_the_generated_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            ident = store.add(account_key(CONFIG), 'uid-1', mail())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result(reply=True))
            store.set_draft(ident, '사람이 고친 초안')
            self.assertEqual(detail_view(store.detail(ident))['draft'], '사람이 고친 초안')
            store.db.close()


class TidyBodyTests(unittest.TestCase):
    """HTML mail turns into text with the source's blank lines and indentation."""

    def test_leading_blank_lines_and_indentation_go(self):
        self.assertEqual(tidy_body('\n\n\n    안녕하세요\n        들여쓰기\n\n'),
                         '안녕하세요\n들여쓰기')

    def test_runs_of_blank_lines_collapse_to_one(self):
        self.assertEqual(tidy_body('첫 줄\n\n\n\n둘째 줄'), '첫 줄\n\n둘째 줄')

    def test_a_paragraph_break_is_kept(self):
        self.assertEqual(tidy_body('가\n\n나'), '가\n\n나')

    def test_nothing_in_nothing_out(self):
        self.assertEqual(tidy_body(''), '')
        self.assertEqual(tidy_body(None), '')
        self.assertEqual(tidy_body('\n\n   \n'), '')


class CalendarEventTests(unittest.TestCase):
    ROWS = [['1:0', 'mail-a', '견적 회신', '', '2026-09-14', '9월 14일까지 회신', '', ''],
            ['2:0', 'mail-b', '정기 점검', '2026-09-20', '', '', '필요', ''],
            ['3:0', 'mail-c', '지난 마감', '', '2026-09-04', '', '', '']]

    def events(self):
        from mail_assistant.calendar_sheet import collect
        return collect(self.ROWS)

    def test_every_entry_becomes_one_all_day_event(self):
        payload = calendar_events(self.events(), TODAY)
        self.assertEqual(len(payload), 3)
        self.assertTrue(all(item['allDay'] for item in payload))
        self.assertEqual([item['start'] for item in payload],
                         ['2026-09-04', '2026-09-14', '2026-09-20'])

    def test_the_kind_picks_the_colour_and_the_evidence_rides_along(self):
        by_mail = {item['id']: item for item in calendar_events(self.events(), TODAY)}
        self.assertEqual(by_mail['mail-a']['extendedProps']['kind'], '마감')
        self.assertEqual(by_mail['mail-a']['extendedProps']['evidence'], '9월 14일까지 회신')
        self.assertEqual(by_mail['mail-b']['extendedProps']['kind'], '확인 필요')
        self.assertNotEqual(by_mail['mail-a']['color'], by_mail['mail-b']['color'])

    def test_a_past_deadline_is_marked_missed(self):
        by_mail = {item['id']: item for item in calendar_events(self.events(), TODAY)}
        self.assertTrue(by_mail['mail-c']['extendedProps']['missed'])
        self.assertFalse(by_mail['mail-a']['extendedProps']['missed'])

    def test_handled_mail_leaves_the_calendar(self):
        payload = calendar_events(self.events(), TODAY, handled={'mail-a'})
        self.assertEqual({item['id'] for item in payload}, {'mail-b', 'mail-c'})

    def test_no_events_is_no_payload(self):
        self.assertEqual(calendar_events({}, TODAY), [])

    def test_the_vendored_calendar_is_where_the_page_asks_for_it(self):
        """The page loads /vendor/fullcalendar/index.global.min.js and nothing else."""
        script = vendor_path() / 'fullcalendar' / 'index.global.min.js'
        self.assertTrue(script.is_file())
        self.assertGreater(script.stat().st_size, 100_000)
        self.assertTrue((vendor_path() / 'fullcalendar' / 'LICENSE.md').is_file())

    def test_the_vendored_font_is_where_the_stylesheet_asks_for_it(self):
        """THEME's @font-face and the preload both point at this one path."""
        face = vendor_path() / 'pretendard' / FONT_FILE
        self.assertTrue(face.is_file())
        self.assertIn(f'/vendor/pretendard/{FONT_FILE}', THEME)
        # OFL-1.1 requires the licence to travel with the font.
        self.assertTrue((vendor_path() / 'pretendard' / 'LICENSE.txt').is_file())

    def test_the_page_asks_for_the_font_before_it_needs_it(self):
        # ECharts paints its labels into a canvas once, so a font that arrives after
        # the first chart never reaches it.
        self.assertIn('rel="preload"', THEME)
        self.assertIn(FONT_FILE, THEME.split('<style>')[0])


class CountdownTests(unittest.TestCase):
    MOMENT = datetime.datetime(2026, 9, 11, 1, 0, 0, tzinfo=datetime.timezone.utc)

    def test_time_left_reads_in_minutes_and_seconds(self):
        self.assertEqual(countdown_text('2026-09-11T00:58:00+00:00', 180, self.MOMENT),
                         '다음 확인까지 1분 0초')
        self.assertEqual(countdown_text('2026-09-11T00:59:30+00:00', 180, self.MOMENT),
                         '다음 확인까지 2분 30초')

    def test_under_a_minute_drops_the_minutes(self):
        self.assertEqual(countdown_text('2026-09-11T00:59:00+00:00', 90, self.MOMENT),
                         '다음 확인까지 30초')

    def test_a_due_or_overdue_cycle_says_so(self):
        self.assertEqual(countdown_text('2026-09-11T00:50:00+00:00', 180, self.MOMENT), '확인 차례')

    def test_nothing_yet_and_nonsense_are_handled(self):
        self.assertEqual(countdown_text('', 180, self.MOMENT), '첫 확인 대기')
        self.assertEqual(countdown_text(None, 180, self.MOMENT), '첫 확인 대기')
        self.assertEqual(countdown_text('언젠가', 180, self.MOMENT), '—')


class CardTargetTests(unittest.TestCase):
    def test_each_actionable_card_goes_somewhere(self):
        self.assertEqual(card_target('미처리 메일', 'tok'), '/mail?t=tok&state=미처리')
        self.assertIn('sort=priority', card_target('긴급·높음', 'tok'))
        self.assertEqual(card_target('7일 내 마감', 'tok'), '/calendar?t=tok')
        self.assertEqual(card_target('검토 전 초안', 'tok'), '/drafts?t=tok')

    def test_the_window_is_part_of_the_card_name(self):
        self.assertEqual(card_target('30일 내 마감', 'tok', within=30), '/calendar?t=tok')
        self.assertIsNone(card_target('30일 내 마감', 'tok', within=7))

    def test_an_unknown_card_is_not_a_link(self):
        self.assertIsNone(card_target('없는 카드', 'tok'))


class RunViewTests(unittest.TestCase):
    def hub(self, folder):
        return Hub(folder, dict(CONFIG), lambda *a, **k: None, report=lambda *a, **k: None)

    def test_a_stopped_worker_reads_as_stopped(self):
        with workspace() as folder:
            hub = self.hub(folder)
            view = run_view(hub, folder, dict(CONFIG, interval=180))
            self.assertEqual(view['label'], '중지됨')
            self.assertEqual(view['countdown'], '중지됨')
            self.assertEqual(view['password'], '확인 불가')
            hub.close()

    def test_the_password_lamp_tells_missing_from_broken(self):
        with workspace() as folder:
            hub = self.hub(folder)

            def absent(email):
                raise LookupError('없음')

            def broken(email):
                raise OSError('자격 증명 저장소 오류')

            self.assertEqual(run_view(hub, folder, CONFIG,
                                      {'read_password': absent})['password'], '없음')
            self.assertEqual(run_view(hub, folder, CONFIG,
                                      {'read_password': broken})['password'], '확인 실패')
            self.assertEqual(run_view(hub, folder, CONFIG,
                                      {'read_password': lambda email: 'x'})['password'], '저장됨')
            hub.close()

    def test_bad_settings_are_listed_as_blockers(self):
        with workspace() as folder:
            hub = self.hub(folder)
            view = run_view(hub, folder, {'host': '', 'port': 'x', 'email': 'nope',
                                          'interval': '1', 'workbook': ''})
            self.assertTrue(view['blockers'])
            self.assertEqual(run_view(hub, folder, dict(CONFIG, interval=180,
                                                        workbook='C:\\a\\b.xlsx'))['blockers'],
                             [])
            hub.close()


class BoardTests(unittest.TestCase):
    """The kanban. A mail card's column is the mail's own 처리 상태."""

    def rows(self, folder):
        store = Store(folder / 'mail.db')
        account = account_key(CONFIG)
        ids = {}
        for uid, action in (('uid-1', '견적 확인'), ('uid-2', '회신 발송'), ('uid-3', '')):
            ident = store.add(account, uid, mail(f'제목 {uid}'))
            ids[uid] = ident
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': f'제목 {uid}', 'attachments': []},
                           dict(result('보통', '2026-09-20'), next_action=action, requests=''))
        return store, ids, account

    def test_a_mail_with_no_next_action_is_not_a_card(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            lanes = board(store.page(account), [])
            texts = [card['text'] for lane in lanes.values() for card in lane]
            self.assertEqual(sorted(texts), ['견적 확인', '회신 발송'])
            store.db.close()

    def test_the_column_follows_the_mails_handled_state(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            store.set_handled(ids['uid-1'], PROGRESS)
            store.set_handled(ids['uid-2'], HANDLED)
            lanes = board(store.page(account), [])
            self.assertEqual([card['text'] for card in lanes[PROGRESS]], ['견적 확인'])
            self.assertEqual([card['text'] for card in lanes[HANDLED]], ['회신 발송'])
            self.assertEqual(lanes[''], [])
            store.db.close()

    def test_a_card_carries_the_deadline_and_the_subject(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            card = next(card for lane in board(store.page(account), []).values()
                        for card in lane if card['text'] == '견적 확인')
            self.assertEqual(card['due'], '2026-09-20')
            self.assertEqual(card['note'], '제목 uid-1')
            self.assertEqual(card['kind'], 'mail')
            store.db.close()

    def test_manual_todos_share_the_board(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            store.add_todo(account, '사무용품 주문', due='2026-09-30')
            second = store.add_todo(account, '회의실 예약')
            store.set_todo_state(second, PROGRESS)
            lanes = board(store.page(account), store.todos(account))
            waiting = [card for card in lanes[''] if card['kind'] == 'todo']
            self.assertEqual([card['text'] for card in waiting], ['사무용품 주문'])
            self.assertEqual(waiting[0]['due'], '2026-09-30')
            self.assertEqual([card['text'] for card in lanes[PROGRESS] if card['kind'] == 'todo'],
                             ['회의실 예약'])
            store.db.close()

    def test_an_unknown_state_lands_in_the_first_column(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            store.db.execute("UPDATE mail SET handled='엉뚱한 상태' WHERE id=?", (ids['uid-1'],))
            store.db.commit()
            lanes = board(store.page(account), [])
            self.assertIn('견적 확인', [card['text'] for card in lanes['']])
            store.db.close()

    def test_a_deleted_todo_leaves_the_board(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            ident = store.add_todo(account, '지울 것')
            store.delete_todo(ident)
            self.assertEqual([todo['text'] for todo in store.todos(account)], [])
            store.db.close()


class DragTests(unittest.TestCase):
    """What a dragged card carries between the two lanes, and what the drop makes of it."""

    def card(self, kind='mail', key='uid-1'):
        return {'kind': kind, 'key': key}

    def test_a_card_moved_to_another_lane_names_its_kind_and_its_id(self):
        payload = drag_payload('', self.card())
        self.assertEqual(drag_drop(payload, HANDLED), ('mail', 'uid-1'))

    def test_an_id_holding_a_colon_survives_the_round_trip(self):
        payload = drag_payload(PROGRESS, self.card(key='<a:b@host>'))
        self.assertEqual(drag_drop(payload, ''), ('mail', '<a:b@host>'))

    def test_a_card_dropped_back_in_its_own_lane_is_not_a_move(self):
        self.assertIsNone(drag_drop(drag_payload(PROGRESS, self.card()), PROGRESS))
        self.assertIsNone(drag_drop(drag_payload('', self.card()), ''))

    def test_a_manual_todo_travels_by_its_row_id(self):
        self.assertEqual(drag_drop(drag_payload('', {'kind': 'todo', 'key': 7}), HANDLED),
                         ('todo', '7'))

    def test_a_todo_id_that_is_not_a_number_never_reaches_the_sql(self):
        self.assertIsNone(drag_drop('todo::일곱', HANDLED))

    def test_a_payload_from_nowhere_is_no_move(self):
        for payload in ('', None, 'mail', 'mail::', '::uid-1', 'other::uid-1'):
            self.assertIsNone(drag_drop(payload, HANDLED), payload)

    def test_the_identity_cannot_close_the_handler_it_sits_in(self):
        handler = drag_start(drag_payload('', self.card(key='he said "go"')))
        self.assertIn(r'\"go\"', handler)
        self.assertEqual(handler.count("setData('text/plain', "), 1)
        self.assertTrue(handler.endswith("}"))


class BoardCountTests(unittest.TestCase):
    """The 현황 tally and the 할 일 판 count the same cards, by construction."""

    def rows(self, folder):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        ids = {}
        for uid, action in (('uid-1', '견적 확인'), ('uid-2', '회신 발송'), ('uid-3', '')):
            ident = store.add(account, uid, mail(f'제목 {uid}'))
            ids[uid] = ident
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': f'제목 {uid}', 'attachments': []},
                           dict(result('보통'), next_action=action, requests=''))
        return store, ids, account

    def test_the_tally_is_the_lane_lengths(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            store.set_handled(ids['uid-1'], HANDLED)
            store.add_todo(account, '사무용품 주문')
            rows, todos = list(store.page(account)), list(store.todos(account))
            counts = board_counts(rows, todos)
            lanes = board(rows, todos)
            for state, _ in (('', ''), (PROGRESS, ''), (HANDLED, '')):
                self.assertEqual(counts[state], len(lanes[state]), state)
            store.db.close()

    def test_a_mail_with_no_next_action_is_in_neither_count(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            counts = board_counts(list(store.page(account)), [])
            self.assertEqual(counts['total'], 2)      # uid-3 has no action
            store.db.close()

    def test_the_bar_measures_the_completed_share(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            store.set_handled(ids['uid-1'], HANDLED)
            counts = board_counts(list(store.page(account)), [])
            self.assertEqual((counts[HANDLED], counts['total'], counts['ratio']), (1, 2, 0.5))
            store.db.close()

    def test_an_empty_board_does_not_divide_by_zero(self):
        self.assertEqual(board_counts([], []), {'': 0, PROGRESS: 0, HANDLED: 0,
                                                'total': 0, 'ratio': 0.0})


class DraftQueueTests(unittest.TestCase):
    def rows(self, folder):
        store = Store(folder / 'mail.db')
        account = account_key(CONFIG)
        ids = {}
        for uid, reply in (('uid-1', True), ('uid-2', False), ('uid-3', True)):
            ident = store.add(account, uid, mail(f'제목 {uid}'))
            ids[uid] = ident
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': f'제목 {uid}', 'attachments': []},
                           result('보통', reply=reply))
        return store, ids, account

    def test_only_mail_that_asked_for_a_reply_queues(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            data = draft_view(store.unedited_drafts(account))
            self.assertEqual({item['id'] for item in data['queue']},
                             {ids['uid-1'], ids['uid-3']})
            store.db.close()

    def test_an_edited_draft_leaves_the_queue(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            store.set_draft(ids['uid-1'], '사람이 고친 초안')
            data = draft_view(store.unedited_drafts(account))
            self.assertEqual([item['id'] for item in data['queue']], [ids['uid-3']])
            store.db.close()

    def test_a_handled_mail_leaves_the_queue(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            store.set_handled(ids['uid-1'], HANDLED)
            self.assertEqual(len(draft_view(store.unedited_drafts(account))['queue']), 1)
            store.db.close()

    def test_the_chosen_mail_is_the_current_one(self):
        with workspace() as folder:
            store, ids, account = self.rows(folder)
            rows = store.unedited_drafts(account)
            data = draft_view(rows, ids['uid-3'])
            self.assertEqual(data['current']['id'], ids['uid-3'])
            self.assertEqual(data['queue'][data['index']]['id'], ids['uid-3'])
            # An id that is no longer queued falls back to the first entry.
            self.assertEqual(draft_view(rows, '없는 id')['index'], 0)
            store.db.close()

    def test_an_empty_queue_has_no_current(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            data = draft_view(store.unedited_drafts(account_key(CONFIG)))
            self.assertEqual(data, {'queue': [], 'current': None, 'index': 0})
            store.db.close()


class ChatContextTests(unittest.TestCase):
    """What a chat turn is allowed to see. The raw message never goes."""

    def test_the_context_is_the_analysis_and_a_trimmed_body(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            ident = store.add(account_key(CONFIG), 'uid-1', mail('견적 요청'))
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '견적 요청',
                                   'body': '본문 ' * 9000, 'attachments': []},
                           result('긴급', '2026-09-20'))
            context = chat_context(store.detail(ident))
            self.assertEqual(context['subject'], '견적 요청')
            self.assertEqual(context['priority'], '긴급')
            self.assertLessEqual(len(context['body']), 8000)
            self.assertNotIn('raw', context)
            self.assertNotIn('draft', context)
            store.db.close()

    def test_no_mail_is_no_context(self):
        self.assertIsNone(chat_context(None))


class StatsViewTests(unittest.TestCase):
    def test_totals_and_a_trend_of_the_asked_for_length(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            ident = store.add(account, 'uid-1', mail())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result('긴급'))
            store.add(account, 'uid-2', mail())
            data = stats_view(store, account, TODAY, days=7)
            self.assertEqual((data['total'], data['waiting'], data['handled']), (2, 1, 0))
            self.assertEqual(len(data['trend']), 7)
            self.assertEqual(data['priorities']['긴급'], 1)
            store.db.close()

    def test_no_account_means_no_trend_queries(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            data = stats_view(store, '', TODAY)
            self.assertEqual(data['trend'], [])
            self.assertEqual(data['total'], 0)
            store.db.close()


class RichTextTests(unittest.TestCase):
    """The Markdown subset a Codex answer gets rendered through."""

    def test_nothing_in_nothing_out(self):
        self.assertEqual(rich_text(''), '')
        self.assertEqual(rich_text(None), '')

    def test_the_answer_cannot_bring_its_own_markup(self):
        html = rich_text('<script>alert(1)</script> & <b>x</b>')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&amp;', html)

    def test_a_blank_line_starts_a_paragraph_and_a_newline_does_not(self):
        self.assertEqual(rich_text('가\n나\n\n다'), '<p>가<br>나</p><p>다</p>')

    def test_bullets_and_numbers_become_their_own_lists(self):
        self.assertEqual(rich_text('- 하나\n- 둘'), '<ul><li>하나</li><li>둘</li></ul>')
        self.assertEqual(rich_text('1. 하나\n2) 둘'), '<ol><li>하나</li><li>둘</li></ol>')

    def test_a_list_after_a_paragraph_keeps_both(self):
        self.assertEqual(rich_text('머리말\n- 하나'),
                         '<p>머리말</p><ul><li>하나</li></ul>')

    def test_bold_and_code_are_the_only_inline_marks(self):
        self.assertEqual(rich_text('**굵게** 와 `코드` 와 _밑줄_'),
                         '<p><b>굵게</b> 와 <code>코드</code> 와 _밑줄_</p>')

    def test_stars_inside_a_code_span_stay_literal(self):
        self.assertEqual(rich_text('`a ** b`'), '<p><code>a ** b</code></p>')

    def test_a_fenced_block_keeps_its_lines_and_still_escapes(self):
        self.assertEqual(rich_text('```\nif a < b:\n    go()\n```'),
                         '<pre><code>if a &lt; b:\n    go()</code></pre>')

    def test_an_answer_cut_off_mid_block_still_renders(self):
        self.assertEqual(rich_text('```\nx = 1'), '<pre><code>x = 1</code></pre>')

    def test_a_heading_is_weight_not_a_heading_tag(self):
        self.assertEqual(rich_text('## 정리'), "<div class='ma-md__h'>정리</div>")


class ChatStoreTests(unittest.TestCase):
    def test_a_thread_per_mail_plus_a_general_one(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            store.add_chat(account, 'user', '일반 질문')
            store.add_chat(account, 'codex', '일반 답')
            store.add_chat(account, 'user', '메일 질문', mail_id='m1')
            self.assertEqual([(role, text) for role, text, _ in store.chat(account)],
                             [('user', '일반 질문'), ('codex', '일반 답')])
            self.assertEqual([text for _, text, _ in store.chat(account, 'm1')], ['메일 질문'])
            store.clear_chat(account)
            self.assertEqual(store.chat(account), [])
            self.assertEqual(len(store.chat(account, 'm1')), 1)
            store.db.close()

    def test_the_limit_keeps_the_newest_but_reads_oldest_first(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            for index in range(5):
                store.add_chat(account, 'user', f'{index}')
            self.assertEqual([text for _, text, _ in store.chat(account, limit=3)],
                             ['2', '3', '4'])
            store.db.close()


class LabelStepTests(unittest.TestCase):
    def test_short_spans_label_every_day(self):
        self.assertEqual(label_step(7), 1)
        self.assertEqual(label_step(10), 1)

    def test_long_spans_thin_the_labels_out(self):
        self.assertEqual(label_step(30), 3)
        self.assertEqual(label_step(90), 9)

    def test_nothing_still_returns_a_usable_step(self):
        self.assertEqual(label_step(0), 1)


class WorkerClaimTests(unittest.TestCase):
    """Only one process may collect. Hub.start() guards inside a process; the mutex
    is what stops the web screens starting a second one beside the window."""

    def test_off_windows_there_is_nothing_to_collide_with(self):
        from mail_assistant import webmain
        with patch.dict('sys.modules', {'win32api': None, 'win32event': None}):
            self.assertEqual(webmain.claim_worker(), (True, None))

    def test_a_free_mutex_is_claimed_and_handed_back_for_closing(self):
        from mail_assistant import webmain
        win32event = types.SimpleNamespace(CreateMutex=lambda *a: 'handle')
        win32api = types.SimpleNamespace(GetLastError=lambda: 0, CloseHandle=lambda h: None)
        with patch.dict('sys.modules', {'win32api': win32api, 'win32event': win32event}):
            self.assertEqual(webmain.claim_worker(), (True, 'handle'))

    def test_a_taken_mutex_refuses_and_closes_its_own_handle(self):
        from mail_assistant import webmain
        closed = []
        win32event = types.SimpleNamespace(CreateMutex=lambda *a: 'handle')
        win32api = types.SimpleNamespace(GetLastError=lambda: webmain.ALREADY_RUNNING,
                                         CloseHandle=closed.append)
        with patch.dict('sys.modules', {'win32api': win32api, 'win32event': win32event}):
            self.assertEqual(webmain.claim_worker(), (False, None))
        self.assertEqual(closed, ['handle'])

    def test_it_is_the_same_name_the_window_uses(self):
        """Two different names would mean two collectors, which is the whole point."""
        from mail_assistant import webmain
        source = (Path(webmain.__file__).parent / '__main__.py').read_text(encoding='utf-8')
        literal = webmain.MUTEX.replace(chr(92), chr(92) * 2)      # as it appears in source
        self.assertIn(literal, source)
        self.assertEqual(webmain.MUTEX, 'Local' + chr(92) + 'HiworksMailAssistant')


class PaletteTests(unittest.TestCase):
    """The palette is keyed by name, so a renamed level would silently lose its colour."""

    def test_every_priority_has_a_status_colour(self):
        self.assertEqual(set(STATUS), {name for name, _ in PRIORITIES})

    def test_card_tones_name_cards_that_exist(self):
        """card_rows() and not ['cards']: 분석 실패 is a card the four-key dict never holds."""
        with tempfile.TemporaryDirectory() as folder:
            data = snapshot(Path(folder), {}, TODAY)
        drawn = {name for name, _ in card_rows(dict(data, failed=1))}
        self.assertLessEqual(set(CARD_TONES), drawn)
        self.assertIn(f'{DUE_DAYS}일 내 마감', CARD_TONES)

    def test_colours_are_css_hex(self):
        for value in list(STATUS.values()) + list(CARD_TONES.values()):
            self.assertRegex(value, r'^#[0-9a-fA-F]{6}$')


class ServerTests(unittest.TestCase):
    def test_open_port_is_free_and_on_loopback_only(self):
        port = open_port()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))     # still free, so the server can take it
        self.assertTrue(1024 < port <= 65535)

    def test_tokens_are_unguessable_and_not_reused(self):
        first, second = new_token(), new_token()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 20)


if __name__ == '__main__':
    unittest.main()
