"""The web 현황 screen's shaping, which needs no nicegui and so runs on every platform."""
import datetime
import re
import socket
import tempfile
import types
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

from mail_assistant.core import (ANALYZING, EVENT_MARK, FAILED, HANDLED, PROGRESS, SORTS,
                                 Store, account_key, event_key, event_row_id, is_event_key)
from mail_assistant.dashboard import PRIORITIES
from mail_assistant.calendar_sheet import MARKERS
from mail_assistant.hub import Hub
from mail_assistant.overview import (BRIEF_DRAFTS, BRIEF_EVENTS, BRIEF_HOUR, BRIEF_OPEN,
                                     BRIEF_TEXT, DUE_DAYS, briefing_due, briefing_input,
                                     due_window, failures, oldest_open, overview)
from mail_assistant.webui import (CARD_TONES, DEFAULT_LIST, FONT_FILE, STATE_TONES, STATUS,
                                  THEME, TREND_LABELS,
                                  NAV_BADGE_MAX, NAV_GROUPS, PAGES, RAIL_BOOT, RAIL_KEY,
                                  RAIL_TOGGLE, SIDE_BREAK, SIDE_RAIL,
                                  SIDE_WIDE, badge_text, bar_status, nav_counts, nav_rows,
                                  rail_css,
                                  update_pill,
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
                                  WINDOW, WINDOW_FLOOR, WINDOW_MIN, window_size,
                                  WEEKDAYS, day_title, list_signature, plain_title,
                                  KIND_ICONS, event_title, reanalyze_text, today_rows,
                                  GENERAL_ROOM, NEW_ROOM, ROOM_ICONS, ROOM_PREVIEW_MAX,
                                  ROOM_TITLE_MAX, room_preview, room_rows, room_search,
                                  room_title,
                                  ANALYSIS_PARTS, EVENT_HINT, PART_TONES, analysis_blocks,
                                  detail_events, event_error, event_kind, event_text,
                                  manual_rows,
                                  BRIEF_LINES, BRIEF_SECTIONS, BRIEF_WATCH, briefing_empty,
                                  briefing_text, briefing_view, stale_text,
                                  DEFAULT_NOTE_COLOR, NOTE_COLORS, NOTE_FILTERS,
                                  PINNED_ON_HOME, keep_note, note_signature,
                                  note_tally, note_tone, note_views, note_wall,
                                  pinned_notes)

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

    def test_every_row_carries_a_kind_and_no_marker(self):
        # The panel draws the kind's icon itself, so the label's marker would be the
        # same thing twice — and it arrived in the link's colour, saying nothing.
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            rows = deadline_rows(overview(store.page(account_key(CONFIG)), TODAY), TODAY)
            self.assertTrue(all(row['kind'] in KIND_ICONS for row in rows))
            self.assertTrue(all(row['title'][:1] not in MARKERS.values() for row in rows))
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
        with workspace() as folder:
            hub = self.hub(folder)
            summary = run_summary(hub, folder, CONFIG)
            self.assertFalse(summary['running'])
            self.assertEqual(summary['label'], '중지됨')
            self.assertEqual(summary['stamps'], {'마지막 확인': '—', '마지막 반영': '—'})
            self.assertEqual(summary['lines'], [])
            hub.close()

    def test_the_strip_shows_the_stored_stamps_and_the_log_tail(self):
        with workspace() as folder:
            directory = folder
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
        with workspace() as folder:
            hub = self.hub(folder)
            hub.stopping.set()
            self.assertEqual(run_summary(hub, folder, CONFIG)['label'], '중지 중…')
            hub.close()

    def test_settings_without_an_account_show_no_stamps_at_all(self):
        with workspace() as folder:
            hub = self.hub(folder)
            self.assertEqual(run_summary(hub, folder, {})['stamps'], {})
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
            ['3:0', 'mail-c', '지난 마감', '', '2026-09-04', '', '', ''],
            ['4:0', 'mail-d', '온라인 웨비나', '2026-09-17T14:00', '', '', '', '']]

    def events(self):
        from mail_assistant.calendar_sheet import collect
        return collect(self.ROWS)

    def test_an_entry_with_no_time_stays_an_all_day_event(self):
        by_mail = {item['id']: item for item in calendar_events(self.events(), TODAY)}
        for ident, start in (('mail-a', '2026-09-14'), ('mail-b', '2026-09-20'),
                             ('mail-c', '2026-09-04')):
            self.assertTrue(by_mail[ident]['allDay'])
            self.assertEqual(by_mail[ident]['start'], start)

    def test_an_entry_that_knows_its_time_is_a_timed_event(self):
        # The 주 view stacked everything in the 종일 band while this was allDay.
        webinar = {item['id']: item for item in calendar_events(self.events(), TODAY)}['mail-d']
        self.assertFalse(webinar['allDay'])
        self.assertEqual(webinar['start'], '2026-09-17T14:00:00')

    def test_a_timed_title_says_its_hour_once(self):
        webinar = {item['id']: item for item in calendar_events(self.events(), TODAY)}['mail-d']
        # The grid slot and the event's own time text already say 14:00.
        self.assertEqual(webinar['title'], '온라인 웨비나')
        # The tooltip has no slot to sit in, so it keeps the hour.
        self.assertEqual(webinar['extendedProps']['clean'], '14:00 온라인 웨비나')

    def test_the_title_drops_the_marker_the_spreadsheet_needs(self):
        payload = calendar_events(self.events(), TODAY)
        self.assertTrue(all(item['title'][:1] not in MARKERS.values() for item in payload))
        self.assertEqual(payload[0]['extendedProps']['clean'], payload[0]['title'])

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
        self.assertEqual({item['id'] for item in payload}, {'mail-b', 'mail-c', 'mail-d'})

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


class SweptCardTests(unittest.TestCase):
    """A mail card taken off the 할 일 판. The mail is untouched; the tally follows."""

    def board_for(self, folder):
        store = Store(folder / 'mail.db')
        account = account_key(CONFIG)
        ident = store.add(account, 'uid-1', mail('제목'))
        store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                       dict(result(), next_action='견적 확인'))
        return store, account, ident

    def test_a_swept_card_leaves_the_board_and_the_tally_together(self):
        with workspace() as folder:
            store, account, ident = self.board_for(folder)
            self.assertEqual(board_counts(store.page(account), [])['total'], 1)
            store.set_todo_hidden(ident)
            lanes = board(store.page(account), [])
            self.assertEqual([card for lane in lanes.values() for card in lane], [])
            self.assertEqual(board_counts(store.page(account), [])['total'], 0)
            store.db.close()

    def test_the_mail_itself_is_left_exactly_as_it_was(self):
        """Sweeping is not 처리 완료 and it is not 삭제; it only hides one card."""
        with workspace() as folder:
            store, account, ident = self.board_for(folder)
            store.set_todo_hidden(ident)
            row = store.detail(ident)
            self.assertEqual(row['handled'], '')
            self.assertIsNotNone(row['result'])
            self.assertEqual(overview(store.page(account), TODAY)['cards']['미처리 메일'], 1)
            store.db.close()

    def test_the_mail_screen_can_put_it_back(self):
        with workspace() as folder:
            store, account, ident = self.board_for(folder)
            store.set_todo_hidden(ident)
            self.assertTrue(detail_view(store.detail(ident))['todo_hidden'])
            store.set_todo_hidden(ident, False)
            self.assertFalse(detail_view(store.detail(ident))['todo_hidden'])
            self.assertEqual(board_counts(store.page(account), [])['total'], 1)
            store.db.close()


class TodayTests(unittest.TestCase):
    """오늘 일정 on the 대시보드, out of the same events the 일정 화면 draws."""

    def data(self, folder, deadline='2026-09-11', handled=''):
        store = Store(folder / 'mail.db')
        account = account_key(CONFIG)
        ident = store.add(account, 'uid-1', mail('제목'))
        store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                       result('긴급', deadline))
        if handled:
            store.set_handled(ident, handled)
        data = overview(store.page(account), TODAY)
        store.db.close()
        return data, ident

    def test_todays_entries_carry_their_kind_and_their_mail(self):
        with workspace() as folder:
            data, ident = self.data(folder)
            rows = today_rows(data['events'], TODAY, data['handled'])
            self.assertEqual([row['kind'] for row in rows], ['마감'])
            self.assertEqual(rows[0]['mail'], ident)
            # The marker the Excel cell needs is stripped: this row has a coloured
            # dot and the kind spelled out beside it already.
            self.assertEqual(rows[0]['title'], '회신')

    def test_another_day_is_not_today(self):
        with workspace() as folder:
            data, _ = self.data(folder, deadline='2026-09-20')
            self.assertEqual(today_rows(data['events'], TODAY, data['handled']), [])

    def test_a_finished_mail_leaves_the_panel_the_way_it_leaves_the_calendar(self):
        with workspace() as folder:
            data, _ = self.data(folder, handled=HANDLED)
            self.assertEqual(today_rows(data['events'], TODAY, data['handled']), [])

    def test_an_empty_day_is_an_empty_list_not_a_key_error(self):
        self.assertEqual(today_rows({}, TODAY), [])

    def test_a_label_with_no_marker_is_left_alone(self):
        self.assertEqual(plain_title('회신 마감'), '회신 마감')
        self.assertEqual(plain_title('■ 14:00 회신 마감'), '14:00 회신 마감')
        self.assertEqual(plain_title(''), '')

    def test_the_date_is_built_by_hand_and_never_by_strftime(self):
        """A Korean strftime format raises UnicodeEncodeError off a Korean PC."""
        self.assertEqual(day_title(datetime.date(2026, 9, 14)), '9월 14일 (월)')
        self.assertEqual(day_title(datetime.date(2026, 9, 13)), '9월 13일 (일)')
        self.assertEqual(len(WEEKDAYS), 7)


class LiveListTests(unittest.TestCase):
    """The 메일 목록 repaints itself, and a repaint is what clears the checkboxes."""

    def store(self, folder):
        store = Store(folder / 'mail.db')
        account = account_key(CONFIG)
        ident = store.add(account, 'uid-1', mail('제목'))
        return store, account, ident

    def signature(self, folder):
        return list_signature(listing(folder, CONFIG, list_state()))

    def test_a_quiet_second_changes_nothing(self):
        with workspace() as folder:
            store, _, _ = self.store(folder)
            store.db.close()
            self.assertEqual(self.signature(folder), self.signature(folder))

    def test_a_mail_entering_codex_is_a_change_the_list_has_to_show(self):
        with workspace() as folder:
            store, account, ident = self.store(folder)
            before = self.signature(folder)
            store.mark_analyzing(account, ident)
            self.assertNotEqual(self.signature(folder), before)
            self.assertIn(ANALYZING, [row['state'] for row
                                      in listing(folder, CONFIG, list_state())['rows']])
            store.db.close()

    def test_an_analysed_verdict_is_a_change_too(self):
        with workspace() as folder:
            store, account, ident = self.store(folder)
            before = self.signature(folder)
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result('긴급'))
            self.assertNotEqual(self.signature(folder), before)
            store.db.close()

    def test_a_deletion_is_a_change(self):
        with workspace() as folder:
            store, _, ident = self.store(folder)
            before = self.signature(folder)
            store.delete([ident])
            self.assertNotEqual(self.signature(folder), before)
            store.db.close()

    def test_a_draft_nobody_can_see_in_the_list_is_not_one(self):
        """The signature is the table's own columns: a repaint for anything else would
        cost the user the rows they had ticked."""
        with workspace() as folder:
            store, _, ident = self.store(folder)
            before = self.signature(folder)
            store.set_draft(ident, '초안을 고쳤습니다')
            self.assertEqual(self.signature(folder), before)
            store.db.close()


class ReanalyzeTextTests(unittest.TestCase):
    """다시 분석 reports what reset() actually did, not what the button meant."""

    def test_everything_asked_for_was_requested(self):
        self.assertEqual(reanalyze_text(3, 3), '3건을 다시 분석하도록 요청했습니다.')

    def test_a_mail_in_codex_right_now_says_so_instead_of_lying(self):
        said = reanalyze_text(0, 1)
        self.assertIn('지금 분석 중', said)
        self.assertNotIn('요청했습니다', said)

    def test_a_mixed_selection_reports_both_halves(self):
        said = reanalyze_text(2, 3)
        self.assertIn('2건을 다시 분석', said)
        self.assertIn('1건은 지금 분석 중', said)

    def test_a_stopped_collector_is_said_out_loud(self):
        # The button queues; the worker is what analyses. With no worker the mail sits
        # at 분석 대기 and the old sentence claimed the work had been requested of
        # something that was not running.
        said = reanalyze_text(3, 3, running=False)
        self.assertIn('3건을 다시 분석하도록 요청했습니다.', said)
        self.assertIn('수집을 시작하면', said)

    def test_a_running_collector_says_nothing_extra(self):
        self.assertEqual(reanalyze_text(3, 3, running=True),
                         '3건을 다시 분석하도록 요청했습니다.')
        self.assertNotIn('수집', reanalyze_text(2, 3, running=True))

    def test_the_skipped_half_still_shows_when_the_collector_is_stopped(self):
        said = reanalyze_text(2, 3, running=False)
        self.assertIn('1건은 지금 분석 중', said)
        self.assertIn('수집을 시작하면', said)


class EventTitleTests(unittest.TestCase):
    """A calendar event says its hour once: in its slot, not again in its title."""

    def entry(self, label, clock=''):
        from mail_assistant.calendar_sheet import Entry
        return Entry(label, '시작', 'm1', 2, '', clock)

    def test_the_clock_comes_off_a_timed_entry(self):
        self.assertEqual(event_title(self.entry('▶ 14:00 웨비나', '14:00')), '웨비나')

    def test_an_untimed_entry_only_loses_its_marker(self):
        self.assertEqual(event_title(self.entry('▶ 착수 회의')), '착수 회의')

    def test_a_title_that_starts_with_its_own_hour_is_left_whole(self):
        # Only the label's own clock comes off, never a title that reads like one.
        self.assertEqual(event_title(self.entry('▶ 14:00 회의', '')), '14:00 회의')

    def test_an_entry_built_without_a_clock_still_works(self):
        from mail_assistant.calendar_sheet import Entry
        self.assertEqual(event_title(Entry('■ 마감', '마감', 'm1', 2)), '마감')


class KindIconTests(unittest.TestCase):
    """One icon and one marker per kind — a kind with neither draws nothing."""

    def test_every_kind_has_an_icon_and_a_marker(self):
        from mail_assistant.calendar_sheet import COLORS
        self.assertEqual(set(KIND_ICONS), set(COLORS))
        self.assertEqual(set(MARKERS), set(COLORS))

    def test_the_markers_are_one_character_each(self):
        # The old ◾/▫/· were one character too, but three sizes; these are one cast.
        self.assertTrue(all(len(mark) == 1 for mark in MARKERS.values()))

    def test_the_three_colours_are_three_colours(self):
        from mail_assistant.calendar_sheet import COLORS
        self.assertEqual(len(set(COLORS.values())), 3)

    def test_the_calendar_page_ships_the_icon_vocabulary_to_the_browser(self):
        from mail_assistant.webui import CALENDAR_SETUP
        self.assertNotIn('__KIND_ICONS__', CALENDAR_SETUP)
        for kind, icon in KIND_ICONS.items():
            self.assertIn(f'"{kind}": "{icon}"', CALENDAR_SETUP)


class LegendTests(unittest.TestCase):
    """The Excel legend colours the mark in front of each label, not a syllable of it."""

    def test_each_span_lands_on_its_own_marker(self):
        from mail_assistant.calendar_sheet import COLORS, legend_text
        text, spans = legend_text()
        self.assertEqual(len(spans), 3)
        for (at, color), kind in zip(spans, ('마감', '시작', '확인 필요')):
            self.assertEqual(text[at - 1], MARKERS[kind])
            self.assertEqual(color, COLORS[kind])


class RoomTitleTests(unittest.TestCase):
    """A 새 대화 is named by its first question, in one line that fits the column."""

    def test_a_short_question_is_the_whole_title(self):
        self.assertEqual(room_title('세금계산서 문의'), '세금계산서 문의')

    def test_newlines_and_runs_of_space_collapse(self):
        # A pasted question arrives with its own line breaks; a title is one row.
        self.assertEqual(room_title('  두 줄짜리\n  질문입니다  '), '두 줄짜리 질문입니다')

    def test_a_long_question_is_cut_with_an_ellipsis(self):
        said = room_title('가' * 80)
        self.assertEqual(len(said), ROOM_TITLE_MAX)
        self.assertTrue(said.endswith('…'))

    def test_nothing_in_nothing_out(self):
        self.assertEqual(room_title(''), '')
        self.assertEqual(room_title(None), '')


class RoomPreviewTests(unittest.TestCase):
    """The list says what was last said, and who said it."""

    def test_my_own_line_is_marked_as_mine(self):
        self.assertEqual(room_preview({'role': 'user', 'last': '언제까지인가요?'}),
                         '나: 언제까지인가요?')

    def test_an_answer_is_marked_as_codex(self):
        self.assertEqual(room_preview({'role': 'codex', 'last': '9월 14일입니다.'}),
                         'Codex: 9월 14일입니다.')

    def test_a_room_with_nothing_in_it_previews_nothing(self):
        self.assertEqual(room_preview({'role': '', 'last': ''}), '')

    def test_a_long_line_is_cut(self):
        said = room_preview({'role': 'user', 'last': '나' * 200})
        self.assertEqual(len(said), ROOM_PREVIEW_MAX)
        self.assertTrue(said.endswith('…'))


class RoomRowTests(unittest.TestCase):
    """Three kinds of thread, and only one of them owns its name."""

    ROOMS = [{'key': '', 'turns': 2, 'at': '2026-09-14T01:00:00+00:00',
              'role': 'codex', 'last': '일반 답'},
             {'key': '#abcd', 'turns': 1, 'at': '2026-09-14T02:00:00+00:00',
              'role': 'user', 'last': '자유 질문', 'name': '세금 문의'},
             {'key': 'a' * 24, 'turns': 4, 'at': '2026-09-14T03:00:00+00:00',
              'role': 'user', 'last': '메일 질문', 'name': '견적 회신 요청'}]

    def test_each_key_becomes_its_own_kind(self):
        rows = room_rows(self.ROOMS)
        self.assertEqual([row['kind'] for row in rows], ['general', 'room', 'mail'])
        self.assertTrue(all(row['kind'] in ROOM_ICONS for row in rows))

    def test_the_general_thread_is_named_for_us(self):
        self.assertEqual(room_rows(self.ROOMS)[0]['title'], GENERAL_ROOM)

    def test_an_unnamed_free_room_reads_as_a_new_one(self):
        rows = room_rows([{'key': '#zz', 'turns': 0, 'at': '', 'role': '', 'last': '',
                           'name': ''}])
        self.assertEqual(rows[0]['title'], NEW_ROOM)
        self.assertEqual(rows[0]['when'], '')

    def test_a_mail_without_a_subject_still_has_a_name(self):
        rows = room_rows([{'key': 'b' * 24, 'turns': 1, 'at': '', 'role': 'user',
                           'last': '질문', 'name': '   '}])
        self.assertEqual(rows[0]['title'], '(제목 없음)')

    def test_exactly_the_open_key_is_marked_open(self):
        rows = room_rows(self.ROOMS, '#abcd')
        self.assertEqual([row['open'] for row in rows], [False, True, False])
        self.assertEqual([row['open'] for row in room_rows(self.ROOMS)],
                         [True, False, False])


class RoomSearchTests(unittest.TestCase):
    """Search reads the title and what was last said, because that is all a row shows."""

    def rows(self):
        return room_rows(RoomRowTests.ROOMS)

    def test_an_empty_query_keeps_everything(self):
        self.assertEqual(len(room_search(self.rows(), '')), 3)
        self.assertEqual(len(room_search(self.rows(), '   ')), 3)

    def test_a_title_match(self):
        found = room_search(self.rows(), '견적')
        self.assertEqual([row['title'] for row in found], ['견적 회신 요청'])

    def test_a_match_on_the_last_line(self):
        found = room_search(self.rows(), '일반 답')
        self.assertEqual([row['kind'] for row in found], ['general'])

    def test_nothing_matching_is_an_empty_list(self):
        self.assertEqual(room_search(self.rows(), '없는말'), [])


class DashboardFollowsTests(unittest.TestCase):
    """Everything the 메일 화면 does has to land on the 대시보드's numbers."""

    def store(self, folder):
        store = Store(folder / 'mail.db')
        account = account_key(CONFIG)
        ident = store.add(account, 'uid-1', mail('제목'))
        store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                       result('긴급', '2026-09-13', reply=True))
        return store, account, ident

    def cards(self, folder):
        return snapshot(folder, CONFIG, TODAY)['cards']

    def test_deleting_a_mail_empties_every_card_it_was_counted_in(self):
        with workspace() as folder:
            store, _, ident = self.store(folder)
            self.assertEqual(self.cards(folder),
                             {'미처리 메일': 1, '긴급·높음': 1, f'{DUE_DAYS}일 내 마감': 1,
                              '검토 전 초안': 1})
            store.delete([ident])
            self.assertEqual(self.cards(folder),
                             {'미처리 메일': 0, '긴급·높음': 0, f'{DUE_DAYS}일 내 마감': 0,
                              '검토 전 초안': 0})
            store.db.close()

    def test_editing_the_draft_takes_it_out_of_the_review_card(self):
        with workspace() as folder:
            store, _, ident = self.store(folder)
            store.set_draft(ident, '사람이 고친 초안')
            self.assertEqual(self.cards(folder)['검토 전 초안'], 0)
            store.db.close()

    def test_marking_it_done_moves_the_open_and_the_deadline_cards(self):
        with workspace() as folder:
            store, _, ident = self.store(folder)
            store.set_handled(ident, HANDLED)
            cards = self.cards(folder)
            self.assertEqual(cards['미처리 메일'], 0)
            self.assertEqual(cards[f'{DUE_DAYS}일 내 마감'], 0)
            store.db.close()

    def test_a_reanalysis_puts_it_back_in_the_waiting_pile(self):
        with workspace() as folder:
            store, _, ident = self.store(folder)
            store.reset([ident], reanalyze=True)
            data = snapshot(folder, CONFIG, TODAY)
            self.assertEqual(data['waiting'], 1)
            self.assertEqual(data['cards']['미처리 메일'], 0)
            store.db.close()

    def test_the_screens_share_one_connection_and_so_see_each_other(self):
        """The write and the read are the same thread's Store — the point of store()."""
        with workspace() as folder:
            store, _, ident = self.store(folder)
            store.db.close()
            from mail_assistant.webui import store as cached
            self.assertEqual(self.cards(folder)['미처리 메일'], 1)
            cached(folder).delete([ident])
            self.assertEqual(self.cards(folder)['미처리 메일'], 0)


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


class SidebarTests(unittest.TestCase):
    """The destinations left the header band for a sidebar, and grew badges."""

    def test_every_page_is_in_exactly_one_group(self):
        listed = [path for _, paths in NAV_GROUPS for path in paths]
        self.assertEqual(sorted(listed), sorted(path for path, _, _ in PAGES))
        self.assertEqual(len(listed), len(set(listed)))

    def test_the_open_page_is_the_only_one_drawn_live(self):
        live = [path for _, items in nav_rows('/todo') for path, _, _, on, _ in items if on]
        self.assertEqual(live, ['/todo'])

    def test_a_page_that_is_not_a_destination_lights_nothing(self):
        live = [path for _, items in nav_rows('/nowhere') for path, _, _, on, _ in items if on]
        self.assertEqual(live, [])

    def test_every_row_carries_the_name_and_icon_pages_gave_it(self):
        drawn = {path: (name, icon)
                 for _, items in nav_rows('/') for path, name, icon, _, _ in items}
        self.assertEqual(drawn, {path: (name, icon) for path, name, icon in PAGES})

    def test_the_first_group_has_no_heading_and_the_rest_do(self):
        headings = [heading for heading, _ in NAV_GROUPS]
        self.assertEqual(headings[0], '')
        self.assertTrue(all(headings[1:]))

    def test_the_rail_is_narrower_than_the_labelled_sidebar(self):
        self.assertLess(SIDE_RAIL, SIDE_WIDE)

    def test_the_rail_takes_over_before_the_window_reaches_its_minimum(self):
        from mail_assistant.webui import WINDOW_MIN
        self.assertGreaterEqual(SIDE_BREAK, WINDOW_MIN[0])

    def test_the_stylesheet_carries_both_widths_and_the_breakpoint(self):
        self.assertIn(f'width:{SIDE_WIDE}px', THEME)
        self.assertIn(f'width:{SIDE_RAIL}px', THEME)
        self.assertIn(f'@media (max-width:{SIDE_BREAK - 1}px)', THEME)


class RailTests(unittest.TestCase):
    """접기: the same rail the breakpoint draws, asked for rather than imposed."""

    def rules(self, scope='html.ma-rail'):
        """The rail as {selector: declarations}, comments dropped."""
        css = re.sub(r'/\*.*?\*/', '', rail_css(scope), flags=re.S)
        return {block.split('{')[0].strip(): block.split('{')[1]
                for block in css.split('}') if '{' in block}

    def test_every_rule_is_scoped_to_what_it_was_asked_for(self):
        for selector in self.rules():
            self.assertTrue(selector.startswith('html.ma-rail '), selector)

    def test_the_two_ways_in_draw_one_rail(self):
        chosen = rail_css('html.ma-rail')
        narrow = rail_css('html:not(.ma-wide)')
        self.assertEqual(chosen.replace('html.ma-rail', '#'),
                         narrow.replace('html:not(.ma-wide)', '#'))

    def test_the_stylesheet_carries_the_choice_and_the_breakpoint(self):
        self.assertIn(rail_css('html.ma-rail'), THEME)
        self.assertIn(rail_css('html:not(.ma-wide)'), THEME)

    def test_the_rail_hides_the_label_and_keeps_the_count(self):
        hidden = ' '.join(selector for selector, rule in self.rules().items()
                          if 'display:none' in rule)
        self.assertIn('.ma-side__label', hidden)
        self.assertNotIn('.ma-badge', hidden)
        # A count that became a dot would be the notification the badge is there for,
        # with the number the reader asked for taken back out.
        self.assertNotIn('font-size:0', self.rules()['html.ma-rail .ma-badge'])

    def test_the_name_survives_the_label_as_the_rows_own_tooltip(self):
        self.assertIn('content:attr(data-name)', rail_css('html.ma-rail'))

    def test_the_toggle_and_the_boot_script_agree_on_where_the_choice_is_kept(self):
        for script in (RAIL_BOOT, RAIL_TOGGLE):
            self.assertIn(f"'{RAIL_KEY}'", script)
            self.assertIn('localStorage', script)

    def test_the_toggle_asks_the_same_breakpoint_the_stylesheet_does(self):
        self.assertIn(f'(max-width:{SIDE_BREAK - 1}px)', RAIL_TOGGLE)

    def test_pressing_it_never_reaches_the_server(self):
        self.assertNotIn('emit(', RAIL_TOGGLE)


class BadgeTests(unittest.TestCase):
    def test_nothing_to_say_draws_no_badge(self):
        for value in (0, None, '', -3):
            self.assertEqual(badge_text(value), '')

    def test_a_count_reads_as_itself(self):
        self.assertEqual(badge_text(7), '7')

    def test_past_the_cap_a_badge_stops_being_a_number(self):
        self.assertEqual(badge_text(NAV_BADGE_MAX), str(NAV_BADGE_MAX))
        self.assertEqual(badge_text(NAV_BADGE_MAX + 1), f'{NAV_BADGE_MAX}+')

    def test_a_value_that_is_not_a_number_says_nothing_rather_than_raising(self):
        self.assertEqual(badge_text('여덟'), '')


class NavCountTests(unittest.TestCase):
    """The badges must be the numbers the 대시보드 already shows, not a second count."""

    def rows(self, directory, **kinds):
        store = Store(directory / 'mail.db')
        account = account_key(CONFIG)
        for index, (subject, answer) in enumerate(kinds.items()):
            ident = store.add(account, f'uid-{index}', mail(subject))
            if answer is not None:
                store.analyzed(ident, {'sender': 'a@b.c', 'subject': subject,
                                       'attachments': []}, answer)
        rows = list(store.page(account))
        todos = list(store.todos(account))
        store.db.close()
        return rows, todos

    def test_the_mail_badge_is_the_unhandled_card(self):
        with workspace() as directory:
            rows, todos = self.rows(directory, 하나=result(), 둘=result('긴급'))
            counts = nav_counts(rows, todos, TODAY)
            self.assertEqual(counts['/mail'], overview(rows, TODAY)['cards']['미처리 메일'])
            self.assertEqual(counts['/mail'], 2)

    def test_the_draft_badge_is_the_review_card(self):
        with workspace() as directory:
            rows, todos = self.rows(directory, 하나=result(reply=True), 둘=result())
            counts = nav_counts(rows, todos, TODAY)
            self.assertEqual(counts['/drafts'], overview(rows, TODAY)['cards']['검토 전 초안'])

    def test_the_todo_badge_counts_what_the_board_still_holds_open(self):
        with workspace() as directory:
            rows, todos = self.rows(directory, 하나=result(), 둘=result())
            lanes = board_counts(rows, todos)
            self.assertEqual(nav_counts(rows, todos, TODAY)['/todo'],
                             lanes['total'] - lanes[HANDLED])

    def test_a_mail_with_no_next_action_is_not_a_todo_badge(self):
        with workspace() as directory:
            answer = result()
            answer['next_action'] = ''
            rows, todos = self.rows(directory, 하나=answer)
            self.assertEqual(nav_counts(rows, todos, TODAY)['/todo'], 0)
            self.assertEqual(nav_counts(rows, todos, TODAY)['/mail'], 1)

    def test_a_mail_still_waiting_for_codex_is_not_counted_anywhere(self):
        with workspace() as directory:
            rows, todos = self.rows(directory, 하나=None)
            counts = nav_counts(rows, todos, TODAY)
            self.assertEqual(set(counts.values()), {0})

    def test_an_empty_mailbox_draws_no_badges(self):
        counts = nav_counts([], [], TODAY)
        self.assertEqual([badge_text(value) for value in counts.values()], ['', '', ''])

    def test_every_counted_path_is_a_real_destination(self):
        paths = {path for path, _, _ in PAGES}
        self.assertLessEqual(set(nav_counts([], [], TODAY)), paths)


class BarStatusTests(unittest.TestCase):
    """The header chip says what 실행 says, in the same three words."""

    def test_no_hub_says_nothing_rather_than_stopped(self):
        self.assertEqual(bar_status(None)['text'], '')

    def test_a_running_worker_beats(self):
        state = bar_status({'running': True, 'stopping': False})
        self.assertEqual(state['text'], '수집 중')
        self.assertTrue(state['beat'])

    def test_a_stopped_worker_is_still_and_says_so(self):
        state = bar_status({'running': False, 'stopping': False})
        self.assertEqual(state['text'], '수집 멈춤')
        self.assertFalse(state['beat'])

    def test_stopping_is_neither_of_the_two(self):
        state = bar_status({'running': True, 'stopping': True})
        self.assertEqual(state['text'], '중지 중…')

    def test_the_words_are_the_ones_the_run_strip_uses(self):
        with workspace() as folder:
            hub = Hub(Path(folder), dict(CONFIG), lambda *a, **k: None,
                      report=lambda *a, **k: None)
            self.assertEqual(bar_status(hub.state())['text'], '수집 멈춤')
            hub.close()

    def test_every_tone_is_css_hex(self):
        for state in (None, {'running': True}, {'running': False}, {'stopping': True}):
            self.assertRegex(bar_status(state)['tone'], r'^#[0-9a-fA-F]{6}$')


class UpdatePillTests(unittest.TestCase):
    def test_nothing_offered_says_nothing(self):
        for offer in (None, {}, '0.5.1'):
            self.assertEqual(update_pill(offer), '')

    def test_an_offer_names_the_version_and_nothing_else(self):
        self.assertEqual(update_pill({'version': '0.5.1', 'size': 42_000_000}),
                         '새 버전 0.5.1')



class AnalysisBlockTests(unittest.TestCase):
    """분석 결과's parts: reading order, nothing empty, accent only where it means something."""

    VIEW = {'summary': '요약문', 'requests': '등록해 주세요',
            'reason': '3일 뒤 행사', 'action': '사전등록하세요'}

    def test_every_part_is_drawn_in_reading_order(self):
        self.assertEqual([part['key'] for part in analysis_blocks(self.VIEW)],
                         ['summary', 'requests', 'reason', 'action'])

    def test_an_empty_part_is_not_drawn_at_all(self):
        view = dict(self.VIEW, requests='', reason='   ')
        self.assertEqual([part['key'] for part in analysis_blocks(view)],
                         ['summary', 'action'])

    def test_an_unanalysed_mail_has_no_parts(self):
        self.assertEqual(analysis_blocks({}), [])

    def test_only_the_two_parts_a_reader_must_act_on_carry_an_accent(self):
        accents = {part['key']: part['accent'] for part in analysis_blocks(self.VIEW)}
        self.assertEqual(accents, {'summary': None, 'requests': 'brand',
                                   'reason': None, 'action': 'ok'})

    def test_every_accent_the_parts_name_has_a_colour(self):
        for _, _, _, accent in ANALYSIS_PARTS:
            self.assertIn(accent, PART_TONES)


class DetailEventTests(unittest.TestCase):
    """The mail's 일정 cards: the calendar's own kind, and why one is not on it."""

    def test_a_deadline_is_마감_and_a_bare_start_is_시작(self):
        self.assertEqual(event_kind({'start': '', 'deadline': '2026-09-20'}), '마감')
        self.assertEqual(event_kind({'start': '2026-09-20', 'deadline': ''}), '시작')

    def test_needs_review_wins_over_a_date_that_parsed(self):
        self.assertEqual(event_kind({'start': '', 'deadline': '2026-09-20',
                                     'needs_review': True}), '확인 필요')

    def test_an_event_with_no_readable_date_is_확인_필요(self):
        self.assertEqual(event_kind({'start': '', 'deadline': ''}), '확인 필요')
        self.assertEqual(event_kind({'start': '다음 주 화요일', 'deadline': ''}), '확인 필요')

    def test_the_card_says_when_the_calendar_cannot_show_it(self):
        shaped = detail_events([{'title': '사전등록', 'start': '', 'deadline': '',
                                 'evidence': '링크', 'needs_review': True},
                                {'title': '웨비나', 'start': '2026-09-17T14:00',
                                 'deadline': '', 'evidence': '', 'needs_review': False}])
        self.assertFalse(shaped[0]['on_calendar'])
        self.assertTrue(shaped[1]['on_calendar'])
        self.assertEqual(shaped[1]['start'], '2026-09-17 14:00')
        self.assertEqual(shaped[0]['deadline'], '—')

    def test_a_kindless_event_still_has_an_icon_and_a_colour(self):
        for event in detail_events([{'title': 'x', 'start': '', 'deadline': ''}]):
            self.assertIn(event['kind'], KIND_ICONS)


class ManualEventTests(unittest.TestCase):
    """직접 추가한 일정: stored on their own, drawn beside the analysed ones."""

    def test_a_key_carries_the_mark_and_reads_back(self):
        self.assertTrue(is_event_key(event_key(7)))
        self.assertEqual(event_row_id(event_key(7)), 7)

    def test_a_mail_id_is_never_mistaken_for_one(self):
        ident = 'a1b2c3d4e5f60718293a4b5c'          # 24 hex, which is what a mail id is
        self.assertFalse(is_event_key(ident))
        self.assertIsNone(event_row_id(ident))
        self.assertNotIn(EVENT_MARK, ident)

    def test_a_manual_event_lands_on_the_calendar_beside_the_analysed_ones(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            ident = store.add(account, 'uid-1', mail())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result(deadline='2026-09-14'))
            store.add_event(account, '팀 회고', '', '2026-09-12', '매달 둘째 주')
            rows = list(store.page(account))
            data = overview(rows, TODAY, events=list(store.events(account)))
            titles = [entry.mail_id for day in data['events'] for entry in data['events'][day]]
            self.assertTrue(any(is_event_key(key) for key in titles))
            self.assertEqual(data['cards'][f'{DUE_DAYS}일 내 마감'], 2)
            store.db.close()

    def test_ticking_a_manual_deadline_marks_its_own_row(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            row = store.add_event(account, '팀 회고', '', '2026-09-12')
            store.set_event_handled(row, HANDLED)
            data = overview([], TODAY, events=list(store.events(account)))
            self.assertIn(event_key(row), data['handled'])
            self.assertEqual(data['cards'][f'{DUE_DAYS}일 내 마감'], 0)
            # The checklist keeps what the card drops, so the row is still there, done.
            self.assertEqual([row['done'] for row in deadline_rows(data, TODAY)], [True])
            store.db.close()

    def test_a_manual_row_is_marked_so_no_panel_links_it_to_a_mail(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            store.add_event(account, '팀 회고', '2026-09-11', '2026-09-11')
            data = overview([], TODAY, events=list(store.events(account)))
            self.assertTrue(all(row['manual'] for row in deadline_rows(data, TODAY)))
            self.assertTrue(all(row['manual']
                                for row in today_rows(data['events'], TODAY, data['handled'])))
            self.assertTrue(all(event['extendedProps']['manual']
                                for event in calendar_events(data['events'], TODAY)))
            store.db.close()

    def test_an_analysed_event_is_not_marked_manual(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            ident = store.add(account, 'uid-1', mail())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           result(deadline='2026-09-14'))
            data = overview(list(store.page(account)), TODAY)
            self.assertTrue(all(not event['extendedProps']['manual']
                                for event in calendar_events(data['events'], TODAY)))
            store.db.close()

    def test_a_deleted_event_leaves_the_calendar(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            row = store.add_event(account, '팀 회고', '', '2026-09-12')
            store.delete_event(row)
            self.assertEqual(list(store.events(account)), [])
            store.db.close()

    def test_a_titleless_event_never_reaches_the_grid(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            store.add_event(account, '   ', '', '2026-09-12')
            self.assertEqual(overview([], TODAY, events=list(store.events(account)))['events'], {})
            store.db.close()

    def test_the_panel_lists_newest_first_and_says_what_is_done(self):
        with workspace() as folder:
            store = Store(folder / 'mail.db')
            account = account_key(CONFIG)
            first = store.add_event(account, '먼저', '', '2026-09-12')
            store.add_event(account, '나중', '2026-09-13 09:30', '')
            store.set_event_handled(first, HANDLED)
            rows = manual_rows(store.events(account))
            self.assertEqual([row['title'] for row in rows], ['나중', '먼저'])
            self.assertEqual(rows[0]['start'], '2026-09-13 09:30')
            self.assertEqual(rows[0]['deadline'], '—')
            self.assertTrue(rows[1]['done'])
            store.db.close()


class EventErrorTests(unittest.TestCase):
    """An event saved with an unreadable date would appear on no calendar and say nothing."""

    def test_a_good_event_has_nothing_to_say(self):
        self.assertEqual(event_error('팀 회고', '', '2026-09-12'), '')
        self.assertEqual(event_error('팀 회고', '2026-09-12 15:00', ''), '')

    def test_a_missing_title_is_refused_first(self):
        self.assertIn('제목', event_error('  ', '', '엉터리'))

    def test_an_unreadable_date_names_the_field_and_the_format(self):
        problem = event_error('팀 회고', '다음 주 화요일', '')
        self.assertIn('시작', problem)
        self.assertIn(EVENT_HINT, problem)
        self.assertIn('마감', event_error('팀 회고', '', '9/12'))

    def test_an_event_with_no_date_at_all_is_refused(self):
        self.assertIn('달력', event_error('팀 회고', '', ''))

    def test_event_text_never_pretends_to_have_a_date(self):
        self.assertEqual(event_text(''), '—')
        self.assertEqual(event_text('내일'), '—')
        self.assertEqual(event_text('2026-09-17T14:00:00+09:00'), '2026-09-17 14:00')


class BriefingInputTests(unittest.TestCase):
    """What 오늘의 AI 브리핑 sends. Analysed answers and counts, never a mail body."""

    BODY = '이 문장은 본문에만 있습니다'

    def rows(self, folder, count=3):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        for index in range(count):
            message = EmailMessage()
            message['From'] = 'a@b.c'
            message['Subject'] = f'제목 {index}'
            message['Date'] = 'Fri, 11 Sep 2026 10:00:00 +0900'
            message.set_content(self.BODY)
            ident = store.add(account, f'uid-{index}', message.as_bytes())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': f'제목 {index}',
                                   'attachments': [], 'body': self.BODY},
                           result('긴급' if index else '낮음', '2026-09-13', True))
        return store

    def payload(self, store):
        return briefing_input(list(store.page(account_key(CONFIG))), TODAY)

    def test_no_mail_body_ever_reaches_the_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            import json
            self.assertNotIn(self.BODY, json.dumps(self.payload(store), ensure_ascii=False))
            store.db.close()

    def test_the_urgent_mail_is_read_out_before_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            priorities = [row['priority'] for row in self.payload(store)['open_mail']]
            self.assertEqual(priorities[0], '긴급')
            self.assertEqual(priorities[-1], '낮음')
            store.db.close()

    def test_a_long_field_is_clipped_rather_than_sent_whole(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            account = account_key(CONFIG)
            ident = store.add(account, 'uid-1', mail())
            verdict = {**result(), 'requests': '가' * 900}
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': '제목', 'attachments': []},
                           verdict)
            sent = briefing_input(list(store.page(account)), TODAY)['open_mail'][0]
            self.assertLessEqual(len(sent['requests']), BRIEF_TEXT + 1)
            self.assertTrue(sent['requests'].endswith('…'))
            store.db.close()

    def test_what_was_cut_is_said_rather_than_implied(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder, count=BRIEF_OPEN + 4)
            cut = self.payload(store)['truncated']['open_mail']
            self.assertEqual(cut, {'shown': BRIEF_OPEN, 'total': BRIEF_OPEN + 4})
            store.db.close()

    def test_every_list_stays_inside_its_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder, count=BRIEF_OPEN + 4)
            sent = self.payload(store)
            self.assertLessEqual(len(sent['open_mail']), BRIEF_OPEN)
            self.assertLessEqual(len(sent['deadlines']), BRIEF_EVENTS)
            self.assertLessEqual(len(sent['unedited_drafts']), BRIEF_DRAFTS)
            store.db.close()

    def test_a_deadline_carries_no_excel_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            for entry in self.payload(store)['deadlines']:
                self.assertNotIn(entry['title'][:1], MARKERS.values())
            store.db.close()

    def test_a_handled_mail_is_not_in_the_briefing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.rows(folder)
            account = account_key(CONFIG)
            done = list(store.page(account))[0]['id']
            store.set_handled(done, HANDLED)
            sent = self.payload(store)
            self.assertNotIn(done, [row['mail_id'] for row in sent['open_mail']])
            store.db.close()

    def test_an_empty_mailbox_still_produces_a_shape(self):
        sent = briefing_input([], TODAY)
        self.assertEqual(sent['open_mail'], [])
        self.assertEqual(sent['truncated']['open_mail'], {'shown': 0, 'total': 0})


class BriefingDueTests(unittest.TestCase):
    """분석 대기가 이기고, 아침 이후 하루 한 번. 버튼만 시각을 건너뛴다."""

    DAY = TODAY.isoformat()

    def test_analysis_always_wins_the_codex_slot(self):
        self.assertFalse(briefing_due('', TODAY, 11, pending=2))
        self.assertFalse(briefing_due('', TODAY, 11, pending=2, asked=True))

    def test_nothing_before_the_morning_hour(self):
        self.assertFalse(briefing_due('', TODAY, BRIEF_HOUR - 1, pending=0))
        self.assertTrue(briefing_due('', TODAY, BRIEF_HOUR, pending=0))

    def test_the_stamp_is_what_makes_it_once_a_day(self):
        self.assertFalse(briefing_due(self.DAY, TODAY, 11, pending=0))
        self.assertTrue(briefing_due('2026-09-10', TODAY, 11, pending=0))

    def test_the_button_skips_the_clock_and_the_stamp(self):
        self.assertTrue(briefing_due(self.DAY, TODAY, 3, pending=0, asked=True))


class BriefingViewTests(unittest.TestCase):
    """A stored briefing as the card reads it, and every way it can be unreadable."""

    STORED = {'headline': '긴급 2건이 남았습니다.',
              'sections': [{'title': '지난 마감', 'lines': ['A사 견적 회신이 6일 지났습니다.']},
                           {'title': '오늘', 'lines': ['', '  ']}],
              'watch': [{'mail_id': 'a' * 24, 'reason': 'A사 견적'},
                        {'mail_id': event_key(3), 'reason': '직접 추가'},
                        {'mail_id': '', 'reason': '없음'}]}

    def stored(self, folder, day=None, data=None):
        store = Store(Path(folder) / 'mail.db')
        store.save_briefing(account_key(CONFIG), day or TODAY.isoformat(),
                            self.STORED if data is None else data)
        return store

    def test_nothing_stored_draws_nothing(self):
        view = briefing_view(None, TODAY)
        self.assertFalse(view['has'])
        self.assertEqual(view['sections'], [])

    def test_the_stored_briefing_comes_back_whole(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.stored(folder)
            view = briefing_view(store.briefing(account_key(CONFIG)), TODAY)
            self.assertTrue(view['has'])
            self.assertEqual(view['headline'], self.STORED['headline'])
            self.assertFalse(view['stale'])
            store.db.close()

    def test_a_section_with_nothing_in_it_is_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.stored(folder)
            view = briefing_view(store.briefing(account_key(CONFIG)), TODAY)
            self.assertEqual([part['title'] for part in view['sections']], ['지난 마감'])
            store.db.close()

    def test_a_pin_with_no_mail_behind_it_is_not_drawn(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.stored(folder)
            view = briefing_view(store.briefing(account_key(CONFIG)), TODAY)
            self.assertEqual([pin['mail_id'] for pin in view['watch']], ['a' * 24])
            store.db.close()

    def test_yesterday_says_so_rather_than_passing_as_today(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.stored(folder, day='2026-09-09')
            view = briefing_view(store.briefing(account_key(CONFIG)), TODAY)
            self.assertTrue(view['stale'])
            self.assertIn('9월 9일', view['stamp'])
            self.assertIn('2일 전', stale_text(view, TODAY))
            store.db.close()

    def test_the_stamp_never_goes_through_a_korean_strftime(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.stored(folder)
            view = briefing_view(store.briefing(account_key(CONFIG)), TODAY)
            self.assertIn('기준', view['stamp'])
            store.db.close()

    def test_unreadable_stored_text_is_an_empty_card_and_not_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            account = account_key(CONFIG)
            store.db.execute('INSERT OR REPLACE INTO briefing VALUES (?,?,?,?)',
                             (account, TODAY.isoformat(), '2026-09-11T09:00:00+09:00', '{'))
            store.db.commit()
            self.assertFalse(briefing_view(store.briefing(account), TODAY)['has'])
            store.db.close()

    def test_a_long_answer_is_cut_to_what_the_card_can_hold(self):
        data = {'headline': 'x',
                'sections': [{'title': f'{index}', 'lines': [f'{n}' for n in range(20)]}
                             for index in range(9)],
                'watch': [{'mail_id': f'{index:024d}', 'reason': 'r'} for index in range(9)]}
        with tempfile.TemporaryDirectory() as folder:
            store = self.stored(folder, data=data)
            view = briefing_view(store.briefing(account_key(CONFIG)), TODAY)
            self.assertEqual(len(view['sections']), BRIEF_SECTIONS)
            self.assertEqual(len(view['sections'][0]['lines']), BRIEF_LINES)
            self.assertEqual(len(view['watch']), BRIEF_WATCH)
            store.db.close()

    def test_only_the_newest_day_is_offered(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            account = account_key(CONFIG)
            for day in ('2026-09-09', '2026-09-11', '2026-09-10'):
                store.save_briefing(account, day, {**self.STORED, 'headline': day})
            self.assertEqual(store.briefing(account)['day'], '2026-09-11')
            store.db.close()

    def test_old_briefings_are_dropped_rather_than_kept_for_ever(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            account = account_key(CONFIG)
            for index in range(Store.BRIEF_KEEP + 5):
                store.save_briefing(account, f'2026-01-{index + 1:02d}', self.STORED)
            kept = store.db.execute('SELECT COUNT(*) FROM briefing WHERE account=?',
                                    (account,)).fetchone()[0]
            self.assertEqual(kept, Store.BRIEF_KEEP)
            store.db.close()


class BriefingSentenceTests(unittest.TestCase):
    """An empty card says why, and 다시 만들기 says what it actually did."""

    def test_an_empty_mailbox_is_not_the_same_as_a_stopped_collector(self):
        self.assertIn('수집된 메일이 없어', briefing_empty(True, 0, 11))
        self.assertIn('수집을 시작하면', briefing_empty(False, 4, 11))

    def test_before_the_morning_hour_the_card_says_when(self):
        self.assertIn(f'{BRIEF_HOUR}시', briefing_empty(True, 4, BRIEF_HOUR - 1))
        self.assertNotIn(f'{BRIEF_HOUR}시', briefing_empty(True, 4, BRIEF_HOUR))

    def test_the_button_is_a_booking_and_says_so_either_way(self):
        self.assertIn('요청', briefing_text(True))
        self.assertIn('수집을 시작하면', briefing_text(False))


class BeamTests(unittest.TestCase):
    """The 브리핑 card's moving edge, which is CSS and nothing else."""

    def test_the_angle_is_registered_or_it_cannot_animate(self):
        self.assertIn('@property --ma-angle', THEME)
        self.assertIn('@keyframes ma-beam', THEME)

    def test_the_beam_is_masked_down_to_the_frame(self):
        self.assertIn('mask-composite:exclude', THEME)
        self.assertIn('-webkit-mask-composite:xor', THEME)

    def test_a_reader_who_asked_for_stillness_gets_it(self):
        rule = THEME.split('@media (prefers-reduced-motion:reduce)', 1)[1].split('}', 1)[0]
        self.assertIn('.ma-beam::before', rule)
        self.assertIn('animation:none', rule)

    def test_only_the_written_card_carries_the_beam(self):
        # A beam on every card is a beam on none — the same arithmetic as 분석 결과's
        # two accents. One class, used once, and the frame it lives on is built outside
        # the refreshable so the lap is never restarted by a repaint.
        self.assertEqual(THEME.count('.ma-beam {'), 1)


def note(ident, text='메모', color='yellow', pinned=0, mail_id='', updated='2026-09-11T01:00:00+00:00'):
    return {'id': ident, 'text': text, 'color': color, 'pinned': pinned,
            'mail_id': mail_id, 'created': updated, 'updated': updated}


class NoteShapeTests(unittest.TestCase):
    """메모. Everything the wall decides is decided here, where Linux can see it."""

    def test_every_colour_key_is_unique_and_the_default_is_one_of_them(self):
        keys = [key for key, _, _, _ in NOTE_COLORS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(DEFAULT_NOTE_COLOR, keys)

    def test_a_colour_the_app_no_longer_offers_reads_as_the_default(self):
        """The value was written by a version of this app; the screen shows it."""
        self.assertEqual(note_tone('teal'), note_tone(DEFAULT_NOTE_COLOR))
        self.assertEqual(note_tone(''), note_tone(DEFAULT_NOTE_COLOR))

    def test_every_colour_carries_a_ground_and_a_darker_edge(self):
        for key, name, ground, edge in NOTE_COLORS:
            with self.subTest(key):
                self.assertTrue(name)
                self.assertRegex(ground, r'^#[0-9a-f]{6}$')
                self.assertRegex(edge, r'^#[0-9a-f]{6}$')
                self.assertLess(int(edge[1:], 16), int(ground[1:], 16))

    def test_a_view_carries_the_paper_and_the_mail_it_belongs_to(self):
        view = note_views([note(1, mail_id='abc')], {'abc': '견적 요청'})[0]
        self.assertEqual(view['subject'], '견적 요청')
        self.assertEqual(view['mail'], 'abc')
        self.assertEqual((view['ground'], view['edge']), note_tone('yellow'))

    def test_a_memo_whose_mail_is_gone_keeps_its_text_and_loses_its_name(self):
        view = note_views([note(1, mail_id='abc')], {})[0]
        self.assertEqual(view['mail'], 'abc')
        self.assertEqual(view['subject'], '')

    def test_the_filters_are_the_same_list_the_counts_are_keyed_by(self):
        views = note_views([note(1, pinned=1), note(2, mail_id='abc'), note(3)])
        self.assertEqual(sorted(note_tally(views)),
                         sorted(kind for kind, _ in NOTE_FILTERS))

    def test_the_counts_are_what_each_filter_actually_keeps(self):
        views = note_views([note(1, pinned=1, mail_id='abc'), note(2, mail_id='b'), note(3)])
        counts = note_tally(views)
        self.assertEqual(counts, {'all': 3, 'pin': 1, 'mail': 2})
        for kind, _ in NOTE_FILTERS:
            with self.subTest(kind):
                self.assertEqual(counts[kind],
                                 len([v for v in views if keep_note(v, kind)]))

    def test_the_filter_runs_before_the_split_and_not_after(self):
        """'고정' must not draw an empty 메모 wall under the pinned one."""
        views = note_views([note(1, pinned=1), note(2), note(3)])
        wall = note_wall(views, 'pin')
        self.assertEqual([v['id'] for v in wall['pinned']], [1])
        self.assertEqual(wall['rest'], [])

    def test_no_filter_puts_every_memo_on_one_of_the_two_walls(self):
        views = note_views([note(1, pinned=1), note(2), note(3, pinned=1)])
        wall = note_wall(views)
        self.assertEqual([v['id'] for v in wall['pinned']], [1, 3])
        self.assertEqual([v['id'] for v in wall['rest']], [2])

    def test_the_signature_moves_on_everything_the_wall_draws(self):
        base = note_views([note(1, mail_id='abc')])
        for changed in (note(1, text='다른 글', mail_id='abc'),
                        note(1, color='blue', mail_id='abc'),
                        note(1, pinned=1, mail_id='abc'),
                        note(1, mail_id='def'),
                        note(2, mail_id='abc')):
            with self.subTest(changed['id']):
                self.assertNotEqual(note_signature(base),
                                    note_signature(note_views([changed])))

    def test_the_signature_ignores_a_clock_the_wall_does_not_draw(self):
        """A repaint costs whoever is typing their caret, so a stamp must not buy one."""
        early = note_views([note(1, updated='2026-09-11T01:00:00+00:00')])
        later = note_views([note(1, updated='2026-09-12T05:00:00+00:00')])
        self.assertNotEqual(early[0]['when'], later[0]['when'])
        self.assertEqual(note_signature(early), note_signature(later))

    def test_the_dashboard_takes_the_pinned_ones_and_stops(self):
        views = note_views([note(i, pinned=1) for i in range(1, 9)] + [note(99)])
        chosen = pinned_notes(views)
        self.assertEqual(len(chosen), PINNED_ON_HOME)
        self.assertTrue(all(view['pinned'] for view in chosen))

    def test_the_wall_reads_what_the_store_wrote(self):
        with workspace() as directory:
            store = Store(directory / 'mail.db')
            account = account_key(CONFIG)
            ident = store.add(account, 'uid-1', mail('견적 요청'))
            store.add_note(account, '붙인 메모', 'green', ident)
            kept = store.add_note(account, '고정한 메모', 'blue')
            store.set_note_pinned(kept)
            rows = list(store.notes(account))
            views = note_views(rows, store.mail_subjects(r['mail_id'] for r in rows))
            store.db.close()
            wall = note_wall(views)
            self.assertEqual([v['text'] for v in wall['pinned']], ['고정한 메모'])
            self.assertEqual(wall['rest'][0]['subject'], '견적 요청')
            self.assertEqual(wall['rest'][0]['ground'], note_tone('green')[0])


if __name__ == '__main__':
    unittest.main()
