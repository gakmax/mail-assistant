"""The web 현황 screen's shaping, which needs no nicegui and so runs on every platform."""
import datetime
import inspect
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
                                 Store, account_key, event_key, event_row_id, is_event_key,
                                 korean_ratio, looks_foreign, text_of_html)
from mail_assistant.dashboard import CATEGORIES, PRIORITIES
from mail_assistant.settings import (GRADES, model_choices, model_rows,
                                     recommended_slug)
from mail_assistant.style import URGENT, css_color
from mail_assistant.usage import (CALM as USAGE_CALM, FULL as USAGE_FULL,
                                  WARN as USAGE_WARN, snapshot as usage_snapshot,
                                  view as usage_view)
from mail_assistant.calendar_sheet import MARKERS
import mail_assistant.webui
from mail_assistant.hub import Hub
from mail_assistant.overview import (BRIEF_DRAFTS, BRIEF_EVENTS, BRIEF_HOUR, BRIEF_OPEN,
                                     BRIEF_TEXT, DUE_DAYS, briefing_due, briefing_input,
                                     due_window, failures, oldest_open, overview)
from mail_assistant.money import entries as money_entries, entry_of, manual_entry, totals
from mail_assistant.webui import (CAL_LOCALE, CAL_WIDTH, DATE_HINT, PICK_WAIT,
                                  TIME_HINT, WEEKDAYS,
                                  CARD_TONES, COST_TONES, DEFAULT_LIST, FONT_FILE, MUTED,
                                  STATE_TONES, STATUS, USAGE_TONES, usage_chip,
                                  THEME, TOAST_MARKS, TREND_LABELS,
                                  NAV_BADGE_MAX, NAV_GROUPS, PAGES, RAIL_BOOT, RAIL_KEY,
                                  RAIL_TOGGLE, SIDE_BREAK, SIDE_EASE, SIDE_GROUP, SIDE_RAIL,
                                  CHAT_CHROME, translated_note, row_value,
                                  BODY_LIMIT, clipped_note, model_option,
                                  DRAFT_NOTE, DRAFT_TONES, DRAFT_WAYS,
                                  draft_input, draft_picks,
                                  SIDE_WIDE, badge_text, bar_status, nav_counts, nav_rows,
                                  rail_css,
                                  update_pill,
                                  BAR_EVENT, BAR_FILTERS, BAR_NOTE, PRIORITY_NAMES,
                                  bar_link, clicked_bar,
                                  bar_option, bar_rows, card_icon, deadline_rows,
                                  detail_view, href, list_state, listing, new_token, open_port,
                                  release_store, run_summary, snapshot,
                                  soft_of, summary_line, tag_cell, tidy_body,
                                  body_blocks,
                                  calendar_events, card_target, countdown_text, run_view,
                                  FAILED_CARD, WAITING, WAIT_LATE, WAIT_NOTE, wait_age,
                                  THREAD_SHOWN, thread_view, thread_line, thread_clip,
                                  ATTACH_NOTE, size_text, attachment_rows,
                                  MONEY_TONES, MONEY_NOTE, MONEY_HINT, MONEY_KINDS,
                                  money_view, TDS_GREEN_500, NEUTRAL,
                                  SENDER_SHOWN, SENDER_SORTS, sender_rows, sender_sort,
                                  sender_search, sender_line, merge_contacts,
                                  note_reorder, stamp, pick_rows, PICK_SHOWN,
                                  board, board_counts, card_hint, card_rows,
                                  HOME_BLOCKS, HOME_COLS, HOME_DEFAULT, HOME_LAYOUT,
                                  home_columns, home_prefs, home_plan, home_reorder,
                                  COUNT_DEFAULT, COUNT_FORMS, COUNT_CARDS, COUNT_NOTES,
                                  CANVAS_FORMS, SEGMENT_TONES,
                                  donut_option, segment_tone, stack_rows,
                                  SLOT_DROP, SLOT_END, SLOT_LEAVE, SLOT_OVER,
                                  slot_drag_start,
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
                                  BRIEF_LINES, BRIEF_MARKS, BRIEF_SECTIONS, BRIEF_TONES,
                                  BRIEF_WATCH, briefing_card, briefing_empty,
                                  briefing_text, briefing_view, stale_text, watch_pins,
                                  PIN_TITLE_MAX, TIP_COLUMN, TIP_TEXT, clip, mail_tip,
                                  tip_cell,
                                  DEFAULT_NOTE_COLOR, NOTE_COLORS, NOTE_FILTERS,
                                  PINNED_ON_HOME, keep_note, note_signature,
                                  note_tally, note_tone, note_views, note_wall,
                                  pinned_notes)

CONFIG = {'host': 'pop3s.hiworks.com', 'port': 995, 'email': 'me@corp.example'}
TODAY = datetime.date(2026, 9, 11)


def mail(subject='제목', sender='sender@example.com', body='본문'):
    message = EmailMessage()
    message['From'] = sender
    message['Subject'] = subject
    message['Date'] = 'Fri, 11 Sep 2026 10:00:00 +0900'
    message.set_content(body)
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


class WaitingCardTests(unittest.TestCase):
    """답장 대기: the card, where it sends you, and the line under its number."""

    def data(self, days=()):
        return {'cards': {'미처리 메일': 3, '긴급·높음': 1, '7일 내 마감': 2, '검토 전 초안': 0},
                'failed': 0, 'oldest': None,
                'reply_wait': [{'id': f'm{n}', 'days': day, 'subject': 's',
                                'sender': '', 'priority': '', 'received': '',
                                'progress': False}
                               for n, day in enumerate(days)]}

    def test_the_card_is_absent_until_something_is_waiting(self):
        self.assertNotIn(WAITING, [name for name, _ in card_rows(self.data())])

    def test_the_card_carries_the_count_and_sits_before_the_failures(self):
        rows = card_rows(dict(self.data(days=(4, 2)), failed=1))
        self.assertEqual(rows[-2], (WAITING, 2))
        self.assertEqual(rows[-1][0], FAILED_CARD)

    def test_the_card_opens_the_same_filter_it_counted(self):
        target = card_target(WAITING, 'tok')
        self.assertIn(f'state={WAITING}', target)
        self.assertIn('sort=received', target)
        # Oldest first: the card's own hint names the worst row, and the list it
        # opens has to put that row at the top rather than at the bottom.
        self.assertIn('desc=0', target)

    def test_the_hint_names_the_worst_row_not_the_newest(self):
        self.assertEqual(card_hint(WAITING, self.data(days=(9, 2))), '가장 오래 기다린 건 9일째')
        self.assertEqual(card_hint(WAITING, self.data()), '')

    def test_the_age_reads_as_a_day_count(self):
        self.assertEqual(wait_age(3), '3일째')

    def test_the_panel_says_out_loud_what_it_cannot_know(self):
        """POP3로는 보낸 메일을 볼 수 없다 — 그 사실이 화면에 있어야 한다."""
        self.assertIn('완료 표시', WAIT_NOTE)
        self.assertGreater(WAIT_LATE, 0)


class MoneyScreenTests(unittest.TestCase):
    """금액 화면이 그리는 모양 — 특히 '세지 않은 줄'이 줄에서도 보이는가."""

    def view(self, items, order_no=''):
        return {'id': 'm1', 'subject': '견적', 'sender': 'kim@x',
                'money': items, 'order_no': order_no}

    def test_every_kind_has_a_tone_and_only_입금_is_green(self):
        """들어오는 돈과 나가는 돈이 한 목록에 선다. 견적은 아직 돈이 아니라 중립이다."""
        self.assertEqual(set(MONEY_TONES), set(MONEY_KINDS))
        self.assertEqual(MONEY_TONES['입금'], TDS_GREEN_500)
        self.assertEqual(MONEY_TONES['견적'], NEUTRAL)

    def test_a_row_that_is_out_of_the_total_says_so_on_the_row(self):
        """합계 밑의 한 문장은 '어느 줄이' 빠졌는지 말하지 않는다."""
        rows = money_view(self.view([
            {'kind': '견적', 'amount': '1000', 'currency': 'KRW', 'needs_review': False},
            {'kind': '견적', 'amount': '약 1000', 'currency': 'KRW', 'needs_review': False},
            {'kind': '견적', 'amount': '1000', 'currency': 'KRW', 'needs_review': True}]))
        self.assertEqual([row['review'] for row in rows], [False, True, True])
        self.assertEqual(rows[0]['text'], '₩1,000')
        # 읽지 못한 줄은 원문 그대로 보여 준다. 빈 칸은 무엇이 문제인지 말하지 않는다.
        self.assertEqual(rows[1]['text'], '약 1000')

    def test_the_screen_says_it_is_not_a_ledger(self):
        """회계 자료가 아니라 모델이 메일에서 옮긴 숫자다."""
        self.assertIn('회계 자료가 아니에요', MONEY_NOTE)
        self.assertIn('합계', MONEY_HINT)

    def test_a_corrected_row_is_marked_as_corrected(self):
        rows = money_view(self.view([{'kind': '청구', 'amount': '900000',
                                      'currency': 'KRW', 'edited': True}]))
        self.assertTrue(rows[0]['edited'])
        self.assertFalse(rows[0]['review'])

    def test_the_order_number_comes_from_the_mail_when_the_line_has_none(self):
        rows = money_view(self.view([{'kind': '견적', 'amount': '1', 'currency': 'KRW'}],
                                    order_no='A26090135'))
        self.assertEqual(rows[0]['order_no'], 'A26090135')


class AttachmentRowTests(unittest.TestCase):
    """첨부 줄 — 열기 전에 알고 싶은 것은 이름과 크기 둘뿐이다."""

    def test_size_reads_the_way_a_file_manager_writes_it(self):
        self.assertEqual(size_text(0), '0B')
        self.assertEqual(size_text(512), '512B')
        self.assertEqual(size_text(82000), '80KB')
        self.assertEqual(size_text(1536), '1.5KB')
        self.assertEqual(size_text(5 * 1024 ** 2), '5.0MB')
        self.assertEqual(size_text(None), '0B')

    def test_the_screen_shows_the_name_the_sender_wrote(self):
        """저장하는 이름과 보여 주는 이름은 일부러 다르다: 파일시스템에 닿는 쪽만
        보낸 사람이 고를 수 없어야 한다."""
        rows = attachment_rows([{'index': 0, 'name': '../견적서.xlsx', 'size': 2048,
                                 'type': 'application/vnd.ms-excel'}])
        self.assertEqual(rows[0]['name'], '../견적서.xlsx')
        self.assertEqual(rows[0]['size'], '2.0KB')
        self.assertFalse(rows[0]['empty'])

    def test_the_note_says_the_helper_does_not_read_them(self):
        self.assertIn('분석에 보내지 않아요', ATTACH_NOTE)


class SenderCardTests(unittest.TestCase):
    """거래처 카드의 모양, 정렬, 검색."""

    TODAY = datetime.date(2026, 9, 15)

    def raw(self, addr, name, total, open_count, waiting, last):
        return {'addr': addr, 'name': name, 'total': total, 'open': open_count,
                'waiting': waiting, 'done': total - open_count,
                'last': f'{last}T00:00:00+00:00', 'first': '2026-08-01T00:00:00+00:00'}

    ROWS = (('kim@buyer.example', '김과장 <kim@buyer.example>', 8, 3, 2, '2026-09-13'),
            ('lee@corp.example', 'lee@corp.example', 4, 4, 0, '2026-09-14'),
            ('park@x.example', '박부장 <park@x.example>', 2, 0, 1, '2026-09-01'))

    def rows(self):
        return sender_rows([self.raw(*args) for args in self.ROWS], self.TODAY)

    def test_a_sender_with_no_display_name_is_named_by_its_address(self):
        rows = {row['addr']: row for row in self.rows()}
        self.assertEqual(rows['kim@buyer.example']['name'], '김과장')
        self.assertTrue(rows['kim@buyer.example']['named'])
        self.assertEqual(rows['lee@corp.example']['name'], 'lee@corp.example')
        self.assertFalse(rows['lee@corp.example']['named'])

    def test_the_foot_says_how_long_it_has_been_quiet(self):
        rows = {row['addr']: row for row in self.rows()}
        self.assertEqual(sender_line(rows['kim@buyer.example']), '마지막 2026-09-13 · 2일 전')
        self.assertIn('오늘', sender_line(sender_rows(
            [self.raw('a@x', 'a@x', 1, 1, 0, '2026-09-15')], self.TODAY)[0]))

    def test_남은_일_순은_기다리게_한_것부터(self):
        """미처리는 '아직 안 봤다'이고 답장 대기는 '상대가 서 있다'이다."""
        order = [row['addr'] for row in sender_sort(self.rows(), 'open')]
        self.assertEqual(order[0], 'kim@buyer.example')      # 대기 2
        self.assertEqual(order[1], 'park@x.example')         # 대기 1
        self.assertEqual(order[2], 'lee@corp.example')       # 대기 0, 미처리 4

    def test_recent_keeps_the_order_the_query_gave(self):
        self.assertEqual([row['addr'] for row in sender_sort(self.rows(), 'recent')],
                         [addr for addr, *_ in self.ROWS])

    def test_search_looks_at_both_the_name_and_the_address(self):
        """읽는 사람이 이름을 기억할지 주소를 기억할지 알 수 없다."""
        self.assertEqual([row['addr'] for row in sender_search(self.rows(), '김과장')],
                         ['kim@buyer.example'])
        self.assertEqual([row['addr'] for row in sender_search(self.rows(), 'CORP')],
                         ['lee@corp.example'])
        self.assertEqual(len(sender_search(self.rows(), '')), 3)
        self.assertEqual(sender_search(self.rows(), '없는이름'), [])


class ThreadStripTests(unittest.TestCase):
    """이 대화 띠 — 무엇을 그리고, 길어지면 어디를 접는가."""

    def rows(self, count, current=0):
        return [{'id': f'm{n}', 'received': f'2026-09-{n + 1:02d}T00:00:00+00:00',
                 'subject': f'제목 {n}', 'sender': 's', 'handled': '', 'result': '{}',
                 'analyzing': '', 'attempts': 0}
                for n in range(count)]

    def view(self, count, current=0):
        return thread_view(self.rows(count), f'm{current}')

    def test_a_lone_mail_is_not_a_conversation(self):
        """'1통 중 1번째'는 읽는 사람이 이미 보고 있는 것을 다시 말하는 것이다."""
        self.assertEqual(self.view(1), [])

    def test_the_line_says_where_you_are(self):
        self.assertEqual(thread_line(self.view(4, current=2)),
                         '전체 4통 · 지금 보는 것은 3번째')
        self.assertEqual(thread_line([]), '')

    def test_exactly_one_row_is_the_open_mail(self):
        rows = self.view(4, current=2)
        self.assertEqual([row['current'] for row in rows], [False, False, True, False])

    def test_a_short_thread_is_not_clipped(self):
        rows = self.view(3)
        self.assertEqual(thread_clip(rows), (rows, 0))

    def test_a_long_thread_keeps_the_newest_turns(self):
        rows = self.view(12, current=11)
        shown, hidden = thread_clip(rows, shown=4)
        self.assertEqual(hidden, 8)
        self.assertEqual([row['nth'] for row in shown], [9, 10, 11, 12])

    def test_the_open_mail_is_never_clipped_away(self):
        """띠가 '3번째'라고 해 놓고 그 줄이 없으면 위치를 틀리게 말하는 것이다."""
        rows = self.view(12, current=0)
        shown, _ = thread_clip(rows, shown=4)
        self.assertTrue(any(row['current'] for row in shown))
        self.assertEqual(shown[0]['nth'], 1)


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

    def test_a_filter_that_is_not_a_bar_on_the_page_falls_back(self):
        state = list_state({'category': '없는 종류', 'priority': 'DROP TABLE'})
        self.assertEqual((state['category'], state['priority']), ('', ''))
        kept = list_state({'category': CATEGORIES[0], 'priority': PRIORITY_NAMES[0]})
        self.assertEqual((kept['category'], kept['priority']),
                         (CATEGORIES[0], PRIORITY_NAMES[0]))

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
        self.assertEqual(collect_text('', True), '가져올 새 메일이 없어요.')


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
            # 'tip' is the web list's own column: the window has no hover to draw.
            # 'waiting' is the window's, and rides here because both are row_view().
            self.assertEqual(set(row), {'id', 'received', 'sender', 'subject', 'category',
                                        'priority', 'state', 'error', 'waiting', 'tip'})


class BarFilterTests(unittest.TestCase):
    """A 대시보드 bar is the way into the mail it counted, and counts the same mail."""

    ROWS = (('공지', '낮음'), ('공지', '긴급'), ('문의', '낮음'), ('문의', '보통'))

    def build(self, folder, rows=ROWS):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        for index, (category, priority) in enumerate(rows):
            ident = store.add(account, f'uid-{index}', mail(f'메일 {index}'))
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': f'메일 {index}',
                                   'attachments': []},
                           dict(result(priority), category=category))
        store.db.close()

    def listed(self, folder, **filters):
        return listing(folder, CONFIG, list_state(filters))

    def test_every_filter_the_charts_offer_is_one_the_list_keeps(self):
        """BAR_FILTERS against the charts and against DEFAULT_LIST, the way STATES is
        held against STATE_SQL: a value offered with no filter behind it lists
        everything and says it is listing one kind.
        """
        self.assertEqual([names for _, _, names in BAR_FILTERS],
                         [CATEGORIES, PRIORITY_NAMES])
        self.assertEqual(PRIORITY_NAMES, tuple(name for name, _ in PRIORITIES))
        for key, _, _ in BAR_FILTERS:
            self.assertIn(key, DEFAULT_LIST)

    def test_a_bar_lists_only_what_it_counted(self):
        with workspace() as folder:
            self.build(folder)
            data = self.listed(folder, category='공지')
            self.assertEqual(data['total'], 2)
            self.assertTrue(all(row['category'] == '공지' for row in data['rows']))
            self.assertEqual(self.listed(folder, priority='낮음')['total'], 2)

    def test_the_list_counts_exactly_what_the_bar_drew(self):
        """The bar and the list it opens read the same columns, so they cannot disagree."""
        with workspace() as folder:
            self.build(folder)
            data = snapshot(folder, CONFIG, TODAY)
            for name in CATEGORIES:
                self.assertEqual(self.listed(folder, category=name)['total'],
                                 data['categories'][name], name)
            for name in PRIORITY_NAMES:
                self.assertEqual(self.listed(folder, priority=name)['total'],
                                 data['priorities'][name], name)

    def test_a_bar_filter_narrows_what_is_already_filtered(self):
        """The link names one filter; whatever else the reader chose here is still on."""
        with workspace() as folder:
            self.build(folder)
            self.assertEqual(self.listed(folder, category='공지',
                                         priority='긴급')['total'], 1)
            self.assertEqual(self.listed(folder, category='공지',
                                         state=HANDLED)['total'], 0)
            self.assertEqual(self.listed(folder, category='공지',
                                         query='메일 1')['total'], 1)

    def test_a_click_is_read_from_the_bar_or_from_its_axis_label(self):
        # A bar carries the name; an axis label carries it as the value, and the count
        # sits in the other field of both.
        self.assertEqual(clicked_bar({'componentType': 'series', 'name': '공지',
                                      'value': 6}, CATEGORIES), '공지')
        self.assertEqual(clicked_bar({'componentType': 'yAxis', 'value': '공지'},
                                     CATEGORIES), '공지')
        # The client may hand a value back inside a list, as a header click does.
        self.assertEqual(clicked_bar({'value': ['낮음']}, PRIORITY_NAMES), '낮음')

    def test_a_click_on_anything_else_filters_nothing(self):
        self.assertEqual(clicked_bar({'componentType': 'grid'}, CATEGORIES), '')
        self.assertEqual(clicked_bar({'name': '없는 종류'}, CATEGORIES), '')
        self.assertEqual(clicked_bar({'name': None, 'value': None}, CATEGORIES), '')
        self.assertEqual(clicked_bar('공지', CATEGORIES), '')
        self.assertEqual(clicked_bar({'value': []}, CATEGORIES), '')

    def test_the_axis_label_is_clickable_because_a_zero_draws_no_bar(self):
        option = bar_option([('공지', 0)])
        self.assertTrue(option['yAxis']['triggerEvent'])

    def test_the_click_carries_back_only_the_fields_it_reads(self):
        self.assertEqual(set(BAR_EVENT), {'componentType', 'name', 'value'})


class BodyBlockTests(unittest.TestCase):
    """원문 splits into text and tables, so a table is drawn as one rather than shown raw."""

    def blocks(self, html):
        return body_blocks(tidy_body(text_of_html(html)))

    def test_a_table_becomes_a_block_of_rows(self):
        blocks = self.blocks('<div>완료했습니다.</div>'
                             '<table><tr><th>수주번호</th><th>공급가액</th></tr>'
                             '<tr><td>A26090135</td><td>90,000</td></tr></table>'
                             '<div>확인 부탁드립니다.</div>')
        self.assertEqual(blocks, [('text', '완료했습니다.'),
                                  ('table', [['수주번호', '공급가액'], ['A26090135', '90,000']]),
                                  ('text', '확인 부탁드립니다.')])

    def test_a_pipe_in_a_cell_is_a_cell_not_a_column(self):
        blocks = self.blocks('<table><tr><th>항목</th><th>비고</th></tr>'
                             '<tr><td>a|b</td><td>c</td></tr></table>')
        self.assertEqual(blocks[0][1][1], ['a|b', 'c'])

    def test_a_line_of_pipes_is_not_a_table(self):
        """Only a header and the rule under it make one: the rest is what the sender wrote."""
        self.assertEqual(body_blocks('|정말| 표가 아닙니다|\n다음 줄'),
                         [('text', '|정말| 표가 아닙니다|\n다음 줄')])

    def test_a_body_with_no_table_is_one_block(self):
        self.assertEqual(body_blocks('안녕하세요\n\n감사합니다'),
                         [('text', '안녕하세요\n\n감사합니다')])

    def test_an_empty_body_has_no_blocks(self):
        self.assertEqual(body_blocks(''), [])
        self.assertEqual(body_blocks(None), [])


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
        self.assertEqual(reanalyze_text(3, 3), '3건을 다시 분석하도록 요청했어요.')

    def test_a_mail_in_codex_right_now_says_so_instead_of_lying(self):
        said = reanalyze_text(0, 1)
        self.assertIn('지금 분석 중', said)
        self.assertNotIn('요청했어요', said)

    def test_a_mixed_selection_reports_both_halves(self):
        said = reanalyze_text(2, 3)
        self.assertIn('2건을 다시 분석', said)
        self.assertIn('1건은 지금 분석 중', said)

    def test_a_stopped_collector_is_said_out_loud(self):
        # The button queues; the worker is what analyses. With no worker the mail sits
        # at 분석 대기 and the old sentence claimed the work had been requested of
        # something that was not running.
        said = reanalyze_text(3, 3, running=False)
        self.assertIn('3건을 다시 분석하도록 요청했어요.', said)
        self.assertIn('수집을 시작하면', said)

    def test_a_running_collector_says_nothing_extra(self):
        self.assertEqual(reanalyze_text(3, 3, running=True),
                         '3건을 다시 분석하도록 요청했어요.')
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
        """card_rows() and not ['cards']: 분석 실패 and 답장 대기 are cards the
        four-key dict never holds, and both are absent until they have a number."""
        with tempfile.TemporaryDirectory() as folder:
            data = snapshot(Path(folder), {}, TODAY)
        drawn = {name for name, _ in card_rows(
            dict(data, failed=1, reply_wait=[{'id': 'a', 'days': 3}]))}
        self.assertLessEqual(set(CARD_TONES), drawn)
        self.assertIn(f'{DUE_DAYS}일 내 마감', CARD_TONES)
        quiet = {name for name, _ in card_rows(dict(data, failed=0, reply_wait=[]))}
        self.assertNotIn(WAITING, quiet)
        self.assertNotIn(FAILED_CARD, quiet)

    def test_colours_are_css_hex(self):
        for value in list(STATUS.values()) + list(CARD_TONES.values()):
            self.assertRegex(value, r'^#[0-9a-fA-F]{6}$')


class RadiusLadderTests(unittest.TestCase):
    """반경은 TDS 의 아홉 단계이고, 그 밖의 값은 원과, 이름이 붙은 둘뿐이다.

    이 테스트가 있는 이유는 사다리가 생기기 전의 상태다 — 2·4·5·6·7·8·9·10·12px
    아홉 값이 토큰 없이 흩어져 있었고, 5px과 7px이 왜 다른지 말하는 규칙이 코드
    어디에도 없었다. 없었기 때문이다. 지금 아홉인 것은 아홉이 다섯보다 나아서가
    아니라 사다리를 우리가 고르지 않기 때문이고, 그래서 TDS 자신이 제 사다리 밖에
    두는 둘(badge 6, S 버튼 10)도 값이 아니라 *이름*으로 들어와 있다.
    """

    LADDER = ('--r-xs', '--r-s', '--r-m', '--r-l', '--r-xl',
              '--r-2xl', '--r-3xl', '--r-4xl', '--r-full')
    NAMED = ('--r-badge', '--r-btn-s')

    def test_the_component_exceptions_are_named(self):
        """6과 10은 사다리에 없다. 이름이 없으면 그냥 흩어진 숫자로 돌아간다."""
        for token in self.NAMED:
            self.assertIn(f'{token}:', THEME, token)

    def test_the_ladder_is_declared_once(self):
        for token in self.LADDER:
            self.assertIn(f'{token}:', THEME, token)

    def test_nothing_rounds_itself_off_the_ladder(self):
        """원(50%)만 예외다. 막대와 스크롤 썸은 제 높이가 반경이라 --r-full을 쓴다."""
        found = re.findall(r'border-radius:\s*([^;}\n]+)', THEME)
        self.assertTrue(found)
        stray = sorted({value.strip() for value in found
                        if 'var(--r-' not in value and value.strip() != '50%'})
        self.assertEqual(stray, [], f'사다리 밖의 반경: {stray}')

    def test_the_old_single_token_is_gone(self):
        """--r 하나만 있던 시절의 이름이 남아 있으면 두 이름이 한 값을 가리킨다."""
        self.assertNotIn('var(--r)', THEME)


class MergeContactTests(unittest.TestCase):
    """집계한 거래처와 적어 둔 거래처가 겹칠 때, 무엇이 무엇을 이기는가."""

    COUNTED = {'addr': 'kim@x.co.kr', 'name': '김과장', 'named': True, 'total': 14,
               'open': 2, 'waiting': 1, 'done': 3, 'last': '2026-09-14', 'quiet': 2,
               'since': '2026-01-02'}

    def test_the_name_comes_from_the_person_and_the_numbers_never_do(self):
        """반대로 하면 카드의 숫자와 그 카드가 여는 목록이 다른 메일을 센다."""
        merged = merge_contacts([dict(self.COUNTED)],
                                [{'id': 7, 'addr': 'kim@x.co.kr',
                                  'name': '대성 김도현 과장', 'memo': '발주 담당'}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['name'], '대성 김도현 과장')
        self.assertEqual(merged[0]['total'], 14)
        self.assertEqual(merged[0]['open'], 2)
        self.assertEqual(merged[0]['memo'], '발주 담당')
        self.assertEqual(merged[0]['contact'], 7)

    def test_a_contact_with_no_name_keeps_the_display_name(self):
        """이름 없이 메모만 적어 둔 경우. 빈 이름으로 덮으면 주소만 남는다."""
        merged = merge_contacts([dict(self.COUNTED)],
                                [{'id': 7, 'addr': 'kim@x.co.kr', 'name': '  ', 'memo': ''}])
        self.assertEqual(merged[0]['name'], '김과장')

    def test_a_contact_with_no_mail_lands_last_and_counts_nothing(self):
        """마지막 수신이 없는 행을 0으로 세면 '가장 오래된 거래처'로 올라선다."""
        merged = merge_contacts([dict(self.COUNTED)],
                                [{'id': 9, 'addr': 'new@y.co.kr', 'name': '새 거래처',
                                  'memo': ''}])
        self.assertEqual([row['addr'] for row in merged], ['kim@x.co.kr', 'new@y.co.kr'])
        self.assertEqual(merged[-1]['total'], 0)
        self.assertIsNone(merged[-1]['quiet'])

    def test_an_aggregate_row_with_no_contact_carries_the_empty_keys(self):
        """화면이 row['memo']와 row['contact']를 언제나 읽는다 — 없으면 KeyError다."""
        merged = merge_contacts([dict(self.COUNTED)], [])
        self.assertEqual(merged[0]['memo'], '')
        self.assertIsNone(merged[0]['contact'])


class NoteReorderTests(unittest.TestCase):
    """메모를 끌어다 놓는 일. 움직이지 않은 드롭은 None 이고, None 은 다시 그리지 않는다."""

    WALL = [3, 1, 2]

    def test_a_card_lands_before_the_one_it_was_dropped_on(self):
        self.assertEqual(note_reorder(self.WALL, 2, 3), [2, 3, 1])

    def test_a_drop_past_the_last_card_puts_it_last(self):
        self.assertEqual(note_reorder(self.WALL, 3, None), [1, 2, 3])

    def test_a_drop_that_moves_nothing_is_none(self):
        """제자리에 놓은 것까지 벽 전체를 다시 쓰면, 이유 없는 깜빡임이 된다."""
        self.assertIsNone(note_reorder(self.WALL, 1, 1))
        self.assertIsNone(note_reorder(self.WALL, 2, None))

    def test_a_drop_onto_the_other_wall_is_none(self):
        """고정과 메모는 두 벽이고, 고정이 어느 벽인지를 정한다 — 끌어서 바꿀 일이 아니다."""
        self.assertIsNone(note_reorder(self.WALL, 1, 99))
        self.assertIsNone(note_reorder(self.WALL, 99, 1))


class StampTests(unittest.TestCase):
    """날짜와 시각 두 칸이 한 값이 되는 자리."""

    def test_a_clock_needs_a_day(self):
        """시각만 적힌 일정은 달력에 올라갈 자리가 없다 — 여기서 버리는 편이 낫다."""
        self.assertEqual(stamp('', '15:00'), '')

    def test_a_day_without_a_clock_is_the_day(self):
        self.assertEqual(stamp('2026-09-25', ''), '2026-09-25')

    def test_both_join_with_one_space(self):
        self.assertEqual(stamp('2026-09-25', '15:00'), '2026-09-25 15:00')


class PickRowTests(unittest.TestCase):
    """메일 붙이기 피커. 적은 것이 없으면 아무것도 내놓지 않는다."""

    ROWS = [{'id': 'a', 'subject': '[수주] A26090135 발주서', 'sender': '김도현 <k@x.kr>'},
            {'id': 'b', 'subject': '9월 정산 내역', 'sender': '정산팀 <acct@y.kr>'}]

    def test_nothing_typed_offers_nothing(self):
        """최근 여섯 통을 미리 펼치면 그 여섯이 답처럼 보이는데, 붙일 메일은 대개
        그 안에 없다."""
        self.assertEqual(pick_rows(self.ROWS, ''), [])
        self.assertEqual(pick_rows(self.ROWS, '   '), [])

    def test_it_looks_in_the_subject_and_the_sender(self):
        self.assertEqual([row['id'] for row in pick_rows(self.ROWS, '발주')], ['a'])
        self.assertEqual([row['id'] for row in pick_rows(self.ROWS, '정산팀')], ['b'])

    def test_it_stops_at_the_cap(self):
        many = [dict(self.ROWS[0], id=str(n)) for n in range(20)]
        self.assertEqual(len(pick_rows(many, '발주')), PICK_SHOWN)


class ManualMoneyTests(unittest.TestCase):
    """손으로 적은 금액 한 줄. 판정은 분석이 읽은 줄의 것과 같아야 한다."""

    @staticmethod
    def row(**over):
        base = {'id': 4, 'mail_id': '', 'kind': '견적', 'currency': 'KRW',
                'amount': '90000', 'label': '공급가액', 'evidence': '전화 견적',
                'day': '2026-09-14', 'created': '2026-09-14 10:00:00'}
        return {**base, **over}

    def test_it_reads_like_a_line_analysis_produced(self):
        entry = manual_entry(self.row())
        self.assertEqual(entry['value'], 90000)
        self.assertFalse(entry['review'])
        self.assertEqual(entry['kind'], '견적')
        self.assertEqual(entry['row'], 4)

    def test_a_number_it_cannot_read_is_dropped_here_too(self):
        """사람이 적었다고 더 믿지 않는다 — 같은 money_value()를 지난다."""
        entry = manual_entry(self.row(amount='약 1,200만'))
        self.assertIsNone(entry['value'])
        self.assertTrue(entry['review'])

    def test_a_currency_nobody_knows_is_out_of_the_total(self):
        entry = manual_entry(self.row(currency='ZZZ'))
        self.assertTrue(entry['review'])
        self.assertEqual(entry['currency'], '기타')

    def test_the_two_sources_add_up_together(self):
        """totals()는 어느 쪽에서 온 줄인지 알지 못한다. 알게 되면 그 차이가 언젠가
        '손으로 적은 것은 좀 더 믿어도 되지 않나'가 된다."""
        found = money_entries([], [self.row(), self.row(amount='약 1,200만')])
        book = totals(found)
        self.assertEqual(book['sums']['KRW']['견적'], 90000)
        self.assertEqual(book['skipped'], 1)

    def test_an_analysed_line_carries_no_row_handle(self):
        """화면이 이 한 칸으로 둘을 가른다."""
        self.assertIsNone(entry_of({'amount': '1000', 'currency': 'KRW', 'kind': '견적'},
                                   {'id': 'm', 'subject': 's', 'sender': '', 'received': '',
                                    'handled': ''})['row'])


class TypeRampTests(unittest.TestCase):
    """글자 크기도 사다리다 — 이것이 반경에서 배운 것을 한 번 더 적용한 자리다.

    열일곱 값이 쓰이고 있었다: 9.5 · 10.5 · 11 · 11.5 · 12 · 12.5 · 13 · 13.5 · 14 ·
    14.5 · 15 · 16 · 16.5 · 18 · 19 · 21 · 25. 11과 11.5가 둘 다 있었고 어느 것이
    무엇이었는지 말하는 규칙은 없었다 — 반경이 아홉 값으로 흩어져 있던 것과 같은
    일이고, 다만 이쪽은 보는 것이 아니라 읽는 것이라 값이 더 비싸다.

    이름은 TDS 의 것이다. body-2 가 산문의 기본이고, body-3 은 한 화면에 쉰 줄이
    서는 표가 받는 칸이며, caption 은 무언가의 밑에 붙는 줄이다.
    """

    RAMP = ('--fs-h1', '--fs-h3', '--fs-h4', '--fs-t1', '--fs-t2',
            '--fs-b2', '--fs-b3', '--fs-cap', '--fs-caps')
    ICONS = ('--ic-s', '--ic-m', '--ic-l')

    def test_the_ramp_is_declared_once(self):
        for token in self.RAMP:
            self.assertIn(f'{token}:', THEME, token)

    def test_an_icon_is_not_type(self):
        """아이콘은 16/20/24 의 제 사다리를 쓴다. 본문 램프에 섞으면 19px 아이콘이
        '본문보다 한 칸 큰 글자'라는 뜻이 되어 버린다."""
        for token in self.ICONS:
            self.assertIn(f'{token}:', THEME, token)

    def test_nothing_sizes_itself_off_the_ramp(self):
        found = re.findall(r'font-size:\s*([^;}\n]+)', THEME)
        self.assertTrue(found)
        stray = sorted({value.strip() for value in found
                        if 'var(--fs-' not in value and 'var(--ic-' not in value
                        and value.strip() != 'inherit'})
        self.assertEqual(stray, [], f'램프 밖의 글자 크기: {stray}')


class ShadowLadderTests(unittest.TestCase):
    """그림자는 넷이고, 평면이 기본이다 — 떠 있는 표면에만 나타난다.

    넷 다 navy-900 의 낮은 알파이고, 각자 제 자리가 있다: 메뉴·tooltip·dialog·toast.
    inner shadow 는 쓰지 않는다 — 눌린 것은 --press overlay 이지 그림자가 아니다.
    """

    LADDER = ('--shadow-1', '--shadow-2', '--shadow-3', '--shadow-toast')

    def test_the_four_are_declared(self):
        for token in self.LADDER:
            self.assertIn(f'{token}:', THEME, token)

    def test_no_box_shadow_carries_its_own_blur(self):
        """none·focus ring·사다리의 넷만 남는다."""
        allowed = {'none'} | {f'var({token})' for token in self.LADDER}
        stray = sorted({value.strip().replace(' !important', '')
                        for value in re.findall(r'box-shadow:\s*([^;}\n]+)', THEME)
                        if value.strip().replace(' !important', '') not in allowed
                        and '0 0 0' not in value})
        self.assertEqual(stray, [], f'사다리 밖의 그림자: {stray}')


class MotionLadderTests(unittest.TestCase):
    """시간은 셋, 커브는 하나. .12s와 .14s가 섞여 있었고 차이에 이유가 없었다."""

    def test_the_three_lengths_and_the_one_curve_are_declared(self):
        for token in ('--dur-fast:', '--dur-base:', '--dur-slow:', '--ease:'):
            self.assertIn(token, THEME, token)

    def test_no_transition_carries_its_own_number(self):
        for rule in re.findall(r'transition:[^;}]+', THEME):
            self.assertNotRegex(rule, r'\d*\.?\d+s',
                                f'토큰을 쓰지 않는 transition: {rule[:70]}')

    def test_the_fold_spends_the_slowest_step(self):
        """SIDE_EASE는 제 숫자를 갖지 않는다 — 접기는 사다리의 한 칸이다."""
        self.assertIn('var(--dur-slow)', SIDE_EASE)
        self.assertIn('var(--ease)', SIDE_EASE)

    def test_ambient_loops_stay_off_the_ladder(self):
        """맥박과 빔은 상호작용이 아니라 배경이라 제 주기를 갖는다."""
        for rule in re.findall(r'animation:[^;}]+', THEME):
            if 'ma-beat' in rule or 'ma-beam' in rule:
                self.assertRegex(rule, r'\ds')


class FocusAndPressTests(unittest.TestCase):
    """키보드가 어디에 서 있는지, 그리고 눌린 것이 어떻게 보이는지.

    전에는 규칙이 한 줄도 없어 전적으로 Quasar 기본값이었다. shoot.ps1은 키를
    누르지 못하므로 이 프로젝트에서 눈으로 확인할 수 없는 항목이기도 하다.
    """

    def test_the_keyboard_ring_is_focus_visible_and_not_focus(self):
        """:focus면 마우스로 누른 것에도 링이 남는다."""
        self.assertIn(':focus-visible', THEME)
        self.assertIn('outline:2px solid var(--brand)', THEME)

    def test_pressed_is_an_overlay_and_disabled_dims_the_whole_node(self):
        self.assertIn('--press:', THEME)
        self.assertIn('--disabled:', THEME)
        self.assertIn('background-image:linear-gradient(var(--press), var(--press))', THEME)
        self.assertIn('opacity:var(--disabled)', THEME)

    def test_the_press_overlay_is_not_drawn_with_a_shadow(self):
        """눌림은 제 바탕 위의 overlay이지 그림자가 아니다 — inner shadow는 쓰지 않는다."""
        self.assertNotIn('box-shadow:inset', THEME)


class NumeralTests(unittest.TestCase):
    """표의 숫자만 자리를 맞춘다. 문장의 숫자까지 맞추면 '미처리 11건'에 틈이 생긴다."""

    def test_prose_is_proportional_by_default(self):
        body = THEME.split('body {', 1)[1].split('}', 1)[0]
        self.assertIn('font-variant-numeric:proportional-nums', body)
        self.assertNotIn('tabular-nums', body)

    def test_the_places_that_line_up_ask_for_it(self):
        rule = THEME.split('font-variant-numeric:tabular-nums', 1)[0].rsplit('}', 1)[-1]
        for name in ('.ma-table', '.ma-sheet', '.ma-kpi__value', '.ma-log', '.ma-due__left'):
            self.assertIn(name, rule, name)


class ToastTests(unittest.TestCase):
    """토스트가 답하는 질문은 '됐나'이고, 그것을 말하는 것은 아이콘이다.

    79곳의 ui.notify가 전부 타입 없는 회색이어서 '저장했어요'와 '저장하지 못했어요'가
    글자를 읽기 전까지 같은 모양이었다 — toast()는 그 하나를 고치려고 있다.
    """

    def test_every_mark_has_an_icon(self):
        self.assertEqual(set(TOAST_MARKS), {'done', 'fail', 'wait'})
        for mark, icon in TOAST_MARKS.items():
            self.assertTrue(icon, mark)

    def test_the_surface_is_the_same_slate_for_all_three(self):
        """색이 아니라 아이콘이 결과를 말한다 — 표면은 hover 카드와 같은 먹색이다.

        TDS 의 toast 규격 그대로다: fill-primary(grey-900) 표면에 radius-l, 그리고
        네 그림자 중 제 이름이 붙은 것. 먹색을 리터럴로 적어 두면 팔레트가 움직일 때
        토스트만 제자리에 남으므로 --ink 로 묻는다.
        """
        surface = THEME.split('.ma-toast {', 1)[1].split('}', 1)[0]
        self.assertIn('background:var(--ink)', surface)
        self.assertIn('border-radius:var(--r-l)', surface)
        self.assertIn('box-shadow:var(--shadow-toast)', surface)
        for mark in TOAST_MARKS:
            self.assertIn(f'.ma-toast--{mark} .q-notification__icon', THEME)

    def test_nothing_calls_ui_notify_behind_the_helper(self):
        """toast()를 지나지 않으면 표시가 없는 회색 한 줄로 돌아간다."""
        source = Path(mail_assistant.webui.__file__).read_text(encoding='utf-8')
        live = [line for line in source.splitlines()
                if 'ui.notify(' in line and 'classes=' not in line
                and not line.strip().startswith(('#', '*', 'await ui.notify'))]
        self.assertEqual(live, [], live)


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

    def test_the_rail_collapses_the_label_rather_than_deleting_it(self):
        """display:none cannot animate, and the fold has to read as one movement."""
        rules = self.rules()
        label = rules['html.ma-rail .ma-side__words, html.ma-rail .ma-side__label']
        self.assertIn('max-width:0', label)
        for selector, rule in rules.items():
            self.assertNotIn('display:none', rule, selector)
        # A count that became a dot would be the notification the badge is there for,
        # with the number the reader asked for taken back out.
        self.assertNotIn('font-size:0', rules['html.ma-rail .ma-badge'])

    def test_the_rail_is_reached_by_moving_and_never_by_jumping(self):
        """Every property the rail changes is one the wide sidebar can animate to.

        The two that were not are the ones this is here for: a label that was
        display:none blinked out on the first frame, and a badge that was in the row's
        flow one moment and on the icon's shoulder the next teleported 150px left
        while the sidebar it belongs to was still sliding shut.
        """
        rules = self.rules()
        self.assertIn(f'height:{SIDE_GROUP}px', THEME)         # …and 1px in the rail
        self.assertIn('height:1px', rules['html.ma-rail .ma-side__group'])
        self.assertNotIn('position:', rules['html.ma-rail .ma-badge'])
        for moved in ('.ma-side {', '.ma-side__item {', '.ma-side__label {',
                      '.ma-side__group {', '.ma-badge {'):
            declarations = THEME.split(moved)[1].split('}')[0]
            self.assertIn(SIDE_EASE, declarations, moved)

    def test_the_rail_centres_its_icons_with_a_length(self):
        """justify-content cannot be animated to; padding can, so the icon travels."""
        rules = self.rules()
        for selector in ('html.ma-rail .ma-side__item', 'html.ma-rail .ma-side__top'):
            self.assertNotIn('justify-content', rules[selector], selector)
        # The rail is 8px padded either side, so this is the icon's own half-gap.
        self.assertIn(f'padding:8px 0 8px {(SIDE_RAIL - 16 - 18) // 2}px',
                      rules['html.ma-rail .ma-side__item'])

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

    def test_the_fold_button_rides_on_the_sidebar_it_folds(self):
        """And not in the header band, which is state rather than navigation."""
        self.assertIn('.ma-side__top', THEME)
        self.assertIn('.ma-side__fold', THEME)
        self.assertNotIn('.ma-bar__toggle', THEME)

    def test_the_rail_puts_the_button_where_the_mark_was(self):
        """A rail has one square at the top, and the hover is what swaps the two."""
        rules = self.rules()
        self.assertIn('position:absolute', rules['html.ma-rail .ma-side__fold'])
        self.assertIn('opacity:0', rules['html.ma-rail .ma-side__fold'])
        self.assertIn('opacity:1', rules['html.ma-rail .ma-side__top:hover .ma-side__fold'])
        self.assertIn('opacity:0', rules['html.ma-rail .ma-side__top:hover .ma-side__brand'])


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


class UsageChipTests(unittest.TestCase):
    """The header's 사용량 chip. usage.py decides the words; this decides the paint."""

    def test_a_quota_nobody_has_read_draws_no_chip_at_all(self):
        for view in (None, {}, usage_view({})):
            self.assertFalse(usage_chip(view)['shown'])

    def test_a_reading_is_shown_with_both_windows_behind_it(self):
        chip = usage_chip(usage_view(usage_snapshot(
            {'rateLimits': {'primary': {'usedPercent': 32, 'windowDurationMins': 300},
                            'secondary': {'usedPercent': 20, 'windowDurationMins': 10080}}})))
        self.assertTrue(chip['shown'])
        self.assertEqual(chip['text'], 'Codex 사용량 32%')
        self.assertIn('주간 한도 20% 사용', chip['tip'])

    def test_a_calm_quota_is_grey_and_a_spent_one_is_the_urgent_hue(self):
        calm = usage_chip(usage_view(usage_snapshot(
            {'rateLimits': {'primary': {'usedPercent': 10}}})))
        self.assertEqual(calm['tone'], MUTED)
        spent = usage_chip(usage_view(usage_snapshot(
            {'rateLimits': {'primary': {'usedPercent': 100},
                            'rateLimitReachedType': 'rate_limit_reached'}})))
        self.assertEqual(spent['tone'], css_color(URGENT))
        self.assertEqual(spent['text'], 'Codex 한도 도달')

    def test_every_level_the_meter_can_report_has_a_colour(self):
        self.assertEqual(set(USAGE_TONES), {USAGE_CALM, USAGE_WARN, USAGE_FULL})


class ModelPickerTests(unittest.TestCase):
    """The 설정 dropdown's own paint, held against the words settings.py hands it."""

    def test_the_usage_half_of_every_grade_carries_a_colour(self):
        self.assertEqual(set(COST_TONES), {cost for _, cost in GRADES})

    def test_the_dropdown_has_a_rule_of_its_own_rather_than_inline_sizes(self):
        self.assertIn('.ma-select', THEME)



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


class TranslationTests(unittest.TestCase):
    """해외영업 메일: which reading the panel opens on, and what it says about itself."""

    def row(self, folder, subject, body, translated=('', '')):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        ident = store.add(account, 'uid-1', mail(subject, 'john@globaltrade.example', body))
        store.analyzed(ident, {'sender': 'john@globaltrade.example', 'subject': subject,
                               'body': body, 'attachments': []}, result())
        if any(translated):
            store.set_translation(ident, *translated)
        view = detail_view(store.detail(ident))
        store.db.close()
        return view

    def test_an_english_mail_is_the_one_the_button_is_offered_for(self):
        with tempfile.TemporaryDirectory() as folder:
            view = self.row(folder, 'Quotation request',
                            'Dear Sir, please quote 200 units of SKU-4410 by 30 October.')
            self.assertTrue(view['foreign'])
            self.assertEqual((view['translated'], view['language']), ('', ''))

    def test_a_korean_mail_with_an_english_signature_is_still_korean(self):
        with tempfile.TemporaryDirectory() as folder:
            view = self.row(folder, '견적 요청',
                            '9월 20일까지 견적서를 보내주시기 바랍니다. 확인 부탁드립니다.\n\n'
                            'Best regards,\nJohn Miller\nGlobalTrade B.V.')
            self.assertFalse(view['foreign'])

    def test_a_stored_translation_comes_back_with_the_language_it_came_from(self):
        with tempfile.TemporaryDirectory() as folder:
            view = self.row(folder, 'Quotation request', 'Dear Sir, please quote.',
                            translated=('영어', '견적을 요청드립니다.'))
            self.assertEqual(view['translated'], '견적을 요청드립니다.')
            self.assertEqual(view['language'], '영어')

    def test_a_clipped_analysis_says_so_in_the_mails_own_numbers(self):
        note = clipped_note(123456)
        self.assertIn('123,456자', note)
        self.assertIn(f'{BODY_LIMIT:,}자', note)
        # 원문은 전체가 남아 있다는 것이 이 줄의 나머지 절반이다.
        self.assertIn('원문', note)

    def test_an_ordinary_mail_has_no_such_line(self):
        self.assertEqual(clipped_note(0), '')
        self.assertEqual(clipped_note(None), '')

    def test_the_note_names_the_language_and_what_not_to_trust_it_for(self):
        self.assertIn('영어 원문을', translated_note('영어'))
        self.assertIn('금액', translated_note('영어'))
        # Codex may not have named one, and a sentence beginning ' 원문을' is worse
        # than one that simply says 원문을.
        self.assertTrue(translated_note('').startswith('원문을'))

    def test_a_row_written_before_the_column_existed_is_not_a_row_that_lost_it(self):
        self.assertEqual(row_value({'id': 'x'}, 'translated'), '')
        self.assertEqual(row_value({'translated': None}, 'translated'), '')
        self.assertEqual(row_value({'translated': '번역'}, 'translated'), '번역')


class DraftMakerTests(unittest.TestCase):
    """초안 만들기: 어떤 말투로 열리고, 무엇을 Codex에 보내는가."""

    def test_it_opens_on_the_free_choices_when_nothing_was_chosen_before(self):
        self.assertEqual(draft_picks(), {'tone': DRAFT_TONES[0], 'way': DRAFT_WAYS[0]})
        self.assertEqual(draft_picks({}), draft_picks(None))

    def test_the_last_choice_is_what_the_next_mail_opens_on(self):
        self.assertEqual(draft_picks({'tone': '간결하게', 'way': '거절'}),
                         {'tone': '간결하게', 'way': '거절'})

    def test_a_choice_this_build_no_longer_offers_falls_back(self):
        """A toggle whose value is not one of its options draws nothing selected."""
        self.assertEqual(draft_picks({'tone': '단호하게', 'way': '호통'}),
                         {'tone': DRAFT_TONES[0], 'way': DRAFT_WAYS[0]})

    def test_every_option_the_screen_offers_is_the_services_own_list(self):
        """The toggle and the prompt read one list, as core.STATES and STATE_SQL do."""
        from mail_assistant import services
        self.assertEqual(DRAFT_TONES, services.DRAFT_TONES)
        self.assertEqual(DRAFT_WAYS, services.DRAFT_WAYS)

    def test_the_mail_goes_as_it_was_written_and_never_as_its_translation(self):
        """The reply is meant to be in the sender's language; a translation is one
        more thing between the two."""
        view = {'subject': 'Quotation request', 'sender': 'john@globaltrade.example',
                'received': '2026-09-15', 'body': 'Please quote 200 units.',
                'translated': '200개 견적을 요청드립니다.', 'summary': '견적 요청',
                'requests': '단가', 'action': '회신'}
        payload = draft_input(view)
        self.assertEqual(payload['body'], 'Please quote 200 units.')
        self.assertNotIn('translated', payload)

    def test_the_two_things_a_reader_would_otherwise_press_to_find_out(self):
        self.assertIn('같은 언어', DRAFT_NOTE)
        self.assertIn('서명', DRAFT_NOTE)

    def test_the_maker_carries_its_own_fold(self):
        """The control sits on the block it opens, as 접기 sits on the sidebar."""
        self.assertIn('.ma-maker {', THEME)
        self.assertIn('.ma-maker__row {', THEME)


class RecommendedModelTests(unittest.TestCase):
    """추천은 슬러그가 아니라 자리다 — 이름을 박으면 몇 주 뒤 없는 모델을 권하게 된다."""

    def cache(self, count):
        return {'models': [{'slug': f'm{i}', 'display_name': f'M{i}', 'priority': i,
                            'visibility': 'list'} for i in range(count)]}

    def rows(self, count, current=''):
        return model_rows(model_choices(self.cache(count)), current)

    def test_exactly_one_row_carries_the_mark(self):
        """둘에 붙으면 추천이 아니라 분류가 된다."""
        for count in range(1, 10):
            marked = [row for row in self.rows(count) if row['recommended']]
            self.assertEqual(len(marked), 1, count)

    def test_it_is_the_middle_of_the_list_and_not_the_strongest(self):
        rows = self.rows(5)
        picked = next(row for row in rows if row['recommended'])
        # 다섯 줄이면 목록의 한가운데가 가운데 등급과 만난다.
        self.assertEqual((picked['power'], picked['cost']), GRADES[len(GRADES) // 2])
        self.assertNotEqual(picked['value'], rows[1]['value'])   # 성능 높음이 아니다

    def test_a_list_too_short_to_have_a_middle_leans_to_the_stronger(self):
        """이 앱에서 가장 나쁜 실패는 틀린 마감이고, 가벼운 모델이 상대 날짜를 놓칠 때 난다.

        가운데 *등급*으로 골랐을 때는 둘만 올라온 주에 추천이 사라졌다. 목록의 중앙값은
        한 개짜리 목록에도 있다.
        """
        self.assertEqual(recommended_slug(model_choices(self.cache(2))), 'm0')
        self.assertEqual(recommended_slug(model_choices(self.cache(1))), 'm0')
        self.assertEqual(recommended_slug([]), '')

    def test_it_moves_with_whatever_codex_is_listing_this_week(self):
        """캐시가 바뀌면 다시 계산된다. 낡을 수 있는 이름이 어디에도 없다."""
        five = next(row for row in self.rows(5) if row['recommended'])['value']
        nine = next(row for row in self.rows(9) if row['recommended'])['value']
        self.assertNotEqual(five, nine)

    def test_a_cache_codex_has_never_written_recommends_nothing(self):
        """고를 것이 없는 화면에서 추천은 고를 수 없는 것을 가리키는 말이 된다."""
        rows = model_rows([], '')
        self.assertEqual([row for row in rows if row['recommended']], [])
        self.assertFalse(rows[0]['recommended'])        # 기본값은 추천이 아니다

    def test_the_default_row_is_kept_and_says_what_it_cannot_say(self):
        """Codex가 한 번도 돈 적 없는 PC에는 고를 목록 자체가 없으므로 이 줄은 남는다."""
        row = model_rows([], '')[0]
        self.assertEqual(row['value'], '')
        self.assertIn('이 화면에 나오지 않아요', row['hint'])

    def test_the_open_list_says_it_and_so_does_the_closed_field(self):
        rows = self.rows(5)
        picked = next(row for row in rows if row['recommended'])
        self.assertTrue(model_option(picked).endswith('· 추천'))
        self.assertFalse(model_option(rows[0]).endswith('· 추천'))


class KoreanRatioTests(unittest.TestCase):
    """Which mail reads as somebody else's language, counted from its letters."""

    def test_a_korean_mail_is_korean(self):
        self.assertEqual(korean_ratio('견적서를 보내주세요'), 1.0)
        self.assertFalse(looks_foreign('견적서를 9월 20일까지 보내주세요.'))

    def test_an_english_mail_is_not(self):
        self.assertEqual(korean_ratio('Please send the quotation.'), 0.0)
        self.assertTrue(looks_foreign('Please send the quotation.'))

    def test_figures_and_part_numbers_do_not_make_a_korean_mail_foreign(self):
        """Letters only: a 수주 mail is mostly digits and digits belong to no language."""
        self.assertFalse(looks_foreign('수주번호 A26090135 공급가액 90,000 납기 2026-09-15'))

    def test_a_mail_with_no_letters_at_all_is_left_alone(self):
        self.assertEqual(korean_ratio(''), 1.0)
        self.assertFalse(looks_foreign(None))


class ChatFillTests(unittest.TestCase):
    """상담 fills the frame it opens in, rather than stopping 200px short of it."""

    def test_the_thread_is_sized_to_the_window_and_not_to_a_number(self):
        self.assertIn(f'height:calc(100vh - {CHAT_CHROME}px)', THEME)

    def test_the_chrome_it_subtracts_is_the_band_and_the_pages_own_padding(self):
        """Measured off the frame at webui.WINDOW: 57 + 20 + 56, and nothing else."""
        self.assertIn('.ma-page { max-width:1240px; margin:0 auto; padding:20px 20px 56px; }',
                      THEME)
        self.assertIn('height:56px', THEME)          # .ma-bar__inner, plus its hairline
        self.assertEqual(CHAT_CHROME, 57 + 20 + 56)


class FoldTests(unittest.TestCase):
    """The 대시보드's 수집 기록, which is shut until somebody asks for it."""

    def test_the_block_opens_by_growing_rather_than_by_appearing(self):
        rules = THEME.split('.ma-fold {')[1].split('}')[0]
        self.assertIn('grid-template-rows:0fr', rules)
        self.assertIn(SIDE_EASE, rules)
        self.assertIn('.ma-fold.is-open { grid-template-rows:1fr; }', THEME)

    def test_the_clipped_child_is_what_carries_the_overflow(self):
        """The rows are the fold; a log drawn over what is under it is not folded."""
        self.assertIn('.ma-fold > * { overflow:hidden; min-height:0; }', THEME)


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


class MailTipTests(unittest.TestCase):
    """The hover card's own shape, drawn in two places and read from one function."""

    def row(self, folder, analysed=True, subject='견적 요청'):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        ident = store.add(account, 'uid-1', mail(subject, 'kim@buyer.example'))
        if analysed:
            store.analyzed(ident, {'sender': 'kim@buyer.example', 'subject': subject,
                                   'attachments': []}, result('긴급'))
        return store, account, ident

    def tip(self, folder, **kwargs):
        store, account, ident = self.row(folder, **kwargs)
        found = mail_tip(store.mail_cards([ident])[ident])
        store.db.close()
        return found

    def test_an_analysed_mail_says_who_what_and_what_next(self):
        with tempfile.TemporaryDirectory() as folder:
            tip = self.tip(folder)
            self.assertEqual(tip['subject'], '견적 요청')
            self.assertEqual(tip['sender'], 'kim@buyer.example')
            self.assertEqual(tip['priority'], '긴급')
            self.assertEqual(tip['action'], '확인')
            self.assertEqual(tip['state'], '미처리')

    def test_a_mail_nobody_has_analysed_still_has_a_card(self):
        with tempfile.TemporaryDirectory() as folder:
            tip = self.tip(folder, analysed=False)
            self.assertEqual(tip['state'], '분석 대기')
            self.assertEqual((tip['summary'], tip['action'], tip['priority']), ('', '', ''))

    def test_a_mail_with_no_subject_is_named_rather_than_blank(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.tip(folder, subject='')['subject'], '(제목 없음)')

    def test_a_paragraph_is_cut_to_something_a_hover_can_hold(self):
        self.assertEqual(clip('가' * 400, TIP_TEXT)[-1], '…')
        self.assertEqual(len(clip('가' * 400, TIP_TEXT)), TIP_TEXT)
        self.assertEqual(clip('한 줄\n  두 줄', 40), '한 줄 두 줄')
        self.assertEqual(clip(None, 40), '')

    def test_the_list_carries_the_card_with_the_row(self):
        with workspace() as folder:
            store, _, _ = self.row(folder)
            store.db.close()
            row = listing(folder, CONFIG, list_state())['rows'][0]
            self.assertEqual(row['tip']['subject'], row['subject'])

    def test_a_rewritten_summary_repaints_the_list(self):
        """Its two texts are in list_signature, or a stale tooltip survives the beat."""
        with workspace() as folder:
            store, _, ident = self.row(folder)
            before = list_signature(listing(folder, CONFIG, list_state()))
            store.analyzed(ident, {'sender': 'kim@buyer.example', 'subject': '견적 요청',
                                   'attachments': []},
                           {**result('긴급'), 'summary': '다시 쓴 요약'})
            store.db.close()
            self.assertNotEqual(list_signature(listing(folder, CONFIG, list_state())),
                                before)


class TipCellTests(unittest.TestCase):
    """The list's hover button is a slot template: it is drawn in the browser."""

    def test_every_line_comes_off_the_row_the_table_is_holding(self):
        template = tip_cell()
        self.assertIn('props.row.tip.subject', template)
        self.assertIn('props.row.tip.action', template)
        self.assertNotIn('emit(', template)

    def test_the_quotes_inside_an_expression_are_single(self):
        """Every expression sits inside a double-quoted attribute or a {{ }}."""
        for part in re.findall(r'\{\{(.*?)\}\}', tip_cell()):
            self.assertNotIn('"', part)
        for part in re.findall(r'(?:v-if|:offset)="(.*?)"', tip_cell()):
            self.assertNotIn('"', part)

    def test_the_press_never_reaches_the_row_underneath(self):
        """Quasar lets a cell's click through, and the row click opens the mail."""
        self.assertIn('@click.stop', tip_cell())

    def test_the_column_is_not_one_of_the_sortable_ones(self):
        self.assertNotIn(TIP_COLUMN, [key for key, _ in LIST_FIELDS])
        self.assertNotIn(TIP_COLUMN, SORTS)


class WatchPinTests(unittest.TestCase):
    """먼저 볼 메일 is named after the mail, and points at one that is still there."""

    STORED = {'headline': '오늘은 긴급 1건입니다.',
              'sections': [{'title': '우선 확인', 'lines': ['A사 견적 회신이 6일 지났습니다.']}],
              'watch': []}

    def built(self, folder):
        store = Store(Path(folder) / 'mail.db')
        account = account_key(CONFIG)
        ident = store.add(account, 'uid-1', mail('A사 견적 회신 요청', 'kim@buyer.example'))
        store.analyzed(ident, {'sender': 'kim@buyer.example', 'subject': 'A사 견적 회신 요청',
                               'attachments': []}, result('긴급'))
        return store, account, ident

    def test_the_pin_reads_the_subject_and_keeps_the_reason_for_the_hover(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _, ident = self.built(folder)
            pins = watch_pins([{'mail_id': ident, 'reason': '6일째 미처리입니다'}],
                              store.mail_cards([ident]))
            self.assertEqual(pins[0]['title'], 'A사 견적 회신 요청')
            self.assertEqual(pins[0]['reason'], '6일째 미처리입니다')
            self.assertEqual(pins[0]['tip']['priority'], '긴급')
            store.db.close()

    def test_a_long_subject_is_a_chip_and_not_a_line(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            account = account_key(CONFIG)
            ident = store.add(account, 'uid-1', mail('가' * 90, 'kim@buyer.example'))
            pins = watch_pins([{'mail_id': ident, 'reason': 'r'}], store.mail_cards([ident]))
            self.assertEqual(len(pins[0]['title']), PIN_TITLE_MAX)
            store.db.close()

    def test_a_pin_on_a_deleted_mail_is_not_drawn(self):
        self.assertEqual(watch_pins([{'mail_id': 'f' * 24, 'reason': 'r'}], {}), [])

    def test_the_card_reads_the_briefing_and_the_mail_in_one_go(self):
        with tempfile.TemporaryDirectory() as folder:
            store, account, ident = self.built(folder)
            store.save_briefing(account, TODAY.isoformat(),
                                {**self.STORED,
                                 'watch': [{'mail_id': ident, 'reason': '6일째'},
                                           {'mail_id': 'f' * 24, 'reason': '지워짐'}]})
            view = briefing_card(store, account, TODAY)
            self.assertEqual([pin['title'] for pin in view['watch']], ['A사 견적 회신 요청'])
            self.assertTrue(view['has'])
            store.db.close()

    def test_no_account_is_an_empty_card_rather_than_a_query(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            self.assertFalse(briefing_card(store, '', TODAY)['has'])
            store.db.close()


class BriefingMarkTests(unittest.TestCase):
    """Four headings in one blue read as one list; the card's own order says which."""

    def test_there_is_a_mark_for_every_section_the_card_can_hold(self):
        self.assertEqual(len(BRIEF_MARKS), BRIEF_SECTIONS)

    def test_every_accent_is_one_the_card_can_draw(self):
        for _, accent in BRIEF_MARKS:
            self.assertIn(accent, BRIEF_TONES)

    def test_the_marks_are_told_apart_by_both_icon_and_hue(self):
        icons = [icon for icon, _ in BRIEF_MARKS]
        self.assertEqual(len(set(icons)), len(icons))
        tones = [BRIEF_TONES[accent] for _, accent in BRIEF_MARKS]
        self.assertEqual(len(set(tones)), len(tones))

    def test_a_section_takes_the_mark_of_the_place_it_is_drawn_in(self):
        """By position, not by title: Codex names its own sections."""
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            account = account_key(CONFIG)
            store.save_briefing(account, TODAY.isoformat(), {
                'headline': 'x', 'watch': [],
                'sections': [{'title': '비어 있음', 'lines': ['']},
                             {'title': '무엇이든', 'lines': ['한 줄']},
                             {'title': '그 다음', 'lines': ['한 줄']}]})
            sections = briefing_view(store.briefing(account), TODAY)['sections']
            # The empty one is dropped, so the survivors take the first two marks.
            self.assertEqual([(part['icon'], part['accent']) for part in sections],
                             [BRIEF_MARKS[0], BRIEF_MARKS[1]])
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


class HomeLayoutTests(unittest.TestCase):
    """화면 배치: the plans, and what a saved choice is allowed to be."""

    def test_every_plan_draws_every_block_exactly_once(self):
        """A block missing from a plan is a card that vanishes when the reader picks
        that column count, and a repeated one is a move() that fights itself. Held the
        way core.STATES is held against STATE_SQL."""
        for cols, plan in HOME_LAYOUT.items():
            keys = [key for column in plan for key in column]
            self.assertEqual(sorted(keys), sorted(HOME_BLOCKS), cols)
            self.assertEqual(len(keys), len(set(keys)), cols)

    def test_the_menu_offers_exactly_the_plans_that_exist(self):
        """A column count with no plan behind it would fall back and say nothing."""
        self.assertEqual(sorted(dict(HOME_COLS)), sorted(HOME_LAYOUT))
        self.assertIn(HOME_DEFAULT, HOME_LAYOUT)

    def test_no_plan_asks_for_more_stacks_than_the_page_builds(self):
        """home() creates three and hides the spare; a fourth would have nowhere to go."""
        for cols, plan in HOME_LAYOUT.items():
            self.assertLessEqual(len(plan), 3, cols)
            self.assertTrue(all(column for column in plan), cols)

    def test_a_saved_choice_this_build_does_not_know_falls_back(self):
        self.assertEqual(home_prefs({'cols': 'three'})['cols'], 'three')
        self.assertEqual(home_prefs({'cols': 'wide'})['cols'], HOME_DEFAULT)
        self.assertEqual(home_prefs({})['cols'], HOME_DEFAULT)
        self.assertEqual(home_prefs(None)['cols'], HOME_DEFAULT)
        self.assertEqual(home_prefs('even')['cols'], HOME_DEFAULT)

    def test_the_default_is_two_equal_columns(self):
        """Three columns are 390px each at 1240px of page, which the 추이 chart and the
        브리핑 have nothing left to be."""
        self.assertEqual(len(home_columns(HOME_DEFAULT)), 2)
        self.assertEqual(len(home_columns('three')), 3)
        self.assertEqual(home_columns('nonsense'), HOME_LAYOUT[HOME_DEFAULT])

    def test_the_split_no_longer_has_a_wide_half(self):
        """The 1.35:1 split is what stopped a card being moved: the left column was the
        charts' and the right the panels', so a card changed shape when it crossed."""
        self.assertNotIn('1.35fr', THEME)
        self.assertIn('.ma-split--three', THEME)
        self.assertIn('.ma-slot', THEME)



class CountFormTests(unittest.TestCase):
    """메일 종류·우선순위: 목록 by default, 막대 still on offer."""

    def test_the_default_is_the_one_that_can_draw_a_zero(self):
        """A 0 bar draws nothing and needed yAxis.triggerEvent to leave an 11px axis
        label as the whole click target; a 0 row is an <a> like every other row."""
        self.assertEqual(COUNT_DEFAULT, 'rows')
        self.assertIn(COUNT_DEFAULT, dict(COUNT_FORMS))

    def test_a_saved_form_this_build_does_not_know_falls_back(self):
        self.assertEqual(home_prefs({'form': 'bars'})['form'], 'bars')
        self.assertEqual(home_prefs({'form': 'donut'})['form'], 'donut')
        self.assertEqual(home_prefs({'form': 'treemap'})['form'], COUNT_DEFAULT)
        self.assertEqual(home_prefs({})['form'], COUNT_DEFAULT)

    def test_a_row_is_styled_as_a_link_and_a_zero_keeps_its_own_tone(self):
        for rule in ('.ma-bars__row', '.ma-bars__track', '.ma-bars__fill',
                     '.ma-bars__num--zero'):
            self.assertIn(rule, THEME)

    def test_every_form_says_what_is_clickable(self):
        """합산 and 도넛 put the click on the legend, not on the picture; the head has
        to say which, or the reader hunts for a target that is not there."""
        self.assertEqual(sorted(COUNT_NOTES), sorted(dict(COUNT_FORMS)))
        for form in CANVAS_FORMS:
            self.assertIn(form, dict(COUNT_FORMS))

    def test_the_two_cards_count_what_the_filters_filter(self):
        for which, (_, _, source, key, names, tones) in COUNT_CARDS.items():
            self.assertIn(which, HOME_BLOCKS)
            self.assertIn(key, DEFAULT_LIST)
            self.assertIn(source, ('categories', 'priorities'))
            self.assertTrue(names)
            if tones:
                self.assertEqual(sorted(tones), sorted(names))

    def test_a_stack_measures_against_the_whole(self):
        """bar_rows scales to the largest; a 합산 segment is a share of the total."""
        self.assertEqual(stack_rows([('가', 1), ('나', 3)]),
                         [('가', 1, 25), ('나', 3, 75)])
        self.assertEqual(stack_rows([('가', 0), ('나', 0)]),
                         [('가', 0, 0), ('나', 0, 0)])

    def test_a_segment_is_coloured_by_its_place_and_never_by_its_count(self):
        """A ramp handed out by rank would encode size twice and repaint the survivors
        every time the numbers moved."""
        self.assertEqual(segment_tone('긴급', 0, STATUS), STATUS['긴급'])
        self.assertEqual(segment_tone('공지', 4), SEGMENT_TONES[4])
        self.assertEqual(segment_tone('공지', 4), segment_tone('공지', 4, {'기타': '#fff'}))
        self.assertEqual(len(SEGMENT_TONES), len(CATEGORIES))

    def test_the_segment_palette_passes_the_checks_it_was_picked_by(self):
        """Six steps of one hue was the first try and failed twice over — 공지 beside
        기타 at ΔE 7.6 for normal vision, and four of six under 3:1 on the card. These
        are the numbers that replaced it, held here rather than admired.
        """
        for tone in SEGMENT_TONES:
            light, chroma, _, _ = self.oklab(tone)
            self.assertTrue(0.43 <= light <= 0.77, f'{tone} lightness {light:.3f}')
            self.assertGreaterEqual(chroma, 0.1, f'{tone} chroma {chroma:.3f}')
        for first, second in zip(SEGMENT_TONES, SEGMENT_TONES[1:]):
            self.assertGreaterEqual(self.separation(first, second), 15,
                                    f'{first} beside {second}')

    @staticmethod
    def oklab(tone):
        """(lightness, chroma, a, b) — the space the palette checks are stated in."""
        red, green, blue = (int(tone[i:i + 2], 16) / 255 for i in (1, 3, 5))

        def linear(channel):
            return (channel / 12.92 if channel <= 0.04045
                    else ((channel + 0.055) / 1.055) ** 2.4)

        red, green, blue = linear(red), linear(green), linear(blue)
        long = (0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue) ** (1 / 3)
        mid = (0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue) ** (1 / 3)
        short = (0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue) ** (1 / 3)
        light = 0.2104542553 * long + 0.7936177850 * mid - 0.0040720468 * short
        axis_a = 1.9779984951 * long - 2.4285922050 * mid + 0.4505937099 * short
        axis_b = 0.0259040371 * long + 0.7827717662 * mid - 0.8086757660 * short
        return light, (axis_a ** 2 + axis_b ** 2) ** 0.5, axis_a, axis_b

    @classmethod
    def separation(cls, first, second):
        """OKLab distance x100, the unit the checks are written in."""
        one, two = cls.oklab(first), cls.oklab(second)
        return 100 * sum((one[i] - two[i]) ** 2 for i in (0, 2, 3)) ** 0.5

    def test_a_donut_keeps_the_zero_in_its_data_and_off_its_labels(self):
        """The slice cannot be drawn, so the legend under it is the click target — but
        the row still has to exist in the option or the tooltip lies about the total."""
        option = donut_option([('긴급', 0), ('높음', 3)], STATUS)
        series = option['series'][0]
        self.assertEqual([item['name'] for item in series['data']], ['긴급', '높음'])
        self.assertEqual(series['data'][0]['value'], 0)
        self.assertFalse(series['label']['show'])
        self.assertIn('padAngle', series)
        self.assertEqual(series['data'][0]['itemStyle']['color'], STATUS['긴급'])

    def test_the_legend_is_what_makes_a_zero_reachable(self):
        """It carries the contrast relief too, which is the less obvious half.

        Three of SEGMENT_TONES sit under 3:1 against the card, and that is allowed only
        while the picture is not the only label — the legend writes the name, the count
        and the share beside every swatch. Drawing 합산 or 도넛 without it would break
        both halves at once: the 0 becomes unreachable and the palette becomes illegal.
        """
        for rule in ('.ma-leg', '.ma-leg__gone', '.ma-leg__name', '.ma-leg__num',
                     '.ma-comp'):
            self.assertIn(rule, THEME)


class HomeArrangeTests(unittest.TestCase):
    """드래그가 만드는 순서와, 저장된 것을 믿지 않고 고치는 자리."""

    def test_nothing_saved_is_the_default_plan(self):
        self.assertEqual(home_plan('even'), [list(k) for k in HOME_LAYOUT['even']])
        self.assertEqual(home_plan('even', {}), [list(k) for k in HOME_LAYOUT['even']])
        self.assertEqual(home_plan('even', {'even': 'rubbish'}),
                         [list(k) for k in HOME_LAYOUT['even']])

    def test_a_saved_order_is_drawn_as_saved(self):
        saved = {'even': [['ranks', 'kinds', 'trend'], ['run', 'memo', 'todo',
                                                        'deadline', 'wait', 'today']]}
        self.assertEqual(home_plan('even', saved), saved['even'])

    def test_a_key_this_build_does_not_know_is_dropped(self):
        saved = {'even': [['trend', 'weather', 'kinds'], list(HOME_LAYOUT['even'][1])]}
        plan = home_plan('even', saved)
        self.assertNotIn('weather', [key for column in plan for key in column])

    def test_a_card_the_saved_order_never_heard_of_still_gets_drawn(self):
        """A block added in a later version must not vanish because somebody dragged
        something once — the rule that keeps a row for a model Codex stopped listing."""
        saved = {'even': [['trend'], ['today']]}
        plan = home_plan('even', saved)
        self.assertEqual(sorted(key for column in plan for key in column),
                         sorted(HOME_BLOCKS))
        self.assertEqual(plan[0][0], 'trend')

    def test_a_duplicate_is_kept_once(self):
        saved = {'even': [['trend', 'trend', 'kinds'], ['kinds', 'today']]}
        plan = home_plan('even', saved)
        keys = [key for column in plan for key in column]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_two_column_counts_do_not_share_an_order(self):
        saved = {'even': [['kinds', 'trend'], list(HOME_LAYOUT['even'][1])]}
        self.assertEqual(home_plan('even', saved)[0][:2], ['kinds', 'trend'])
        self.assertEqual(home_plan('three', saved), [list(k) for k in HOME_LAYOUT['three']])

    def test_a_plan_always_has_the_column_count_it_was_asked_for(self):
        saved = {'three': [['trend'], ['today']]}
        self.assertEqual(len(home_plan('three', saved)), 3)
        self.assertEqual(len(home_plan('even', {'even': [['a'], ['b'], ['c']]})), 2)

    def test_a_card_moves_inside_its_column(self):
        plan = [['trend', 'kinds'], ['today', 'wait']]
        self.assertEqual(home_reorder(plan, 'kinds', 0, 'trend'),
                         [['kinds', 'trend'], ['today', 'wait']])

    def test_a_card_moves_between_columns(self):
        plan = [['trend', 'kinds'], ['today', 'wait']]
        self.assertEqual(home_reorder(plan, 'kinds', 1, 'wait'),
                         [['trend'], ['today', 'kinds', 'wait']])

    def test_a_drop_on_the_column_appends(self):
        plan = [['trend', 'kinds'], ['today']]
        self.assertEqual(home_reorder(plan, 'trend', 1),
                         [['kinds'], ['today', 'trend']])

    def test_a_drop_that_moves_nothing_is_none(self):
        """None for the reason drag_drop() gives None for a card dropped into its own
        lane: rewriting the stored order and repainting reads as a flicker."""
        plan = [['trend', 'kinds'], ['today']]
        self.assertIsNone(home_reorder(plan, 'kinds', 0, 'kinds'))
        self.assertIsNone(home_reorder(plan, 'trend', 0, 'kinds'))
        self.assertIsNone(home_reorder(plan, 'today', 1))

    def test_a_drop_that_cannot_be_read_is_none(self):
        plan = [['trend', 'kinds'], ['today']]
        self.assertIsNone(home_reorder(plan, '', 0, 'trend'))
        self.assertIsNone(home_reorder(plan, 'weather', 0, 'trend'))
        self.assertIsNone(home_reorder(plan, 'trend', 7))
        self.assertIsNone(home_reorder(plan, 'trend', 1, 'kinds'))

    def test_only_the_drop_talks_to_the_server(self):
        """dragover fires once per frame; a Python handler on it turns one card's
        travel into hundreds of websocket messages."""
        for handler in (SLOT_OVER, SLOT_LEAVE, SLOT_END, slot_drag_start('trend')):
            self.assertNotIn('emit(', handler)
        self.assertIn('emit(', SLOT_DROP)

    def test_the_slot_keeps_the_drop_from_its_own_column(self):
        """The stack under a card is a drop target too, and without this a drop *on* a
        card would also be a drop at the end of the column."""
        self.assertIn('stopPropagation', SLOT_DROP)

    def test_the_handle_drags_the_card_and_not_the_band(self):
        start = slot_drag_start('trend')
        self.assertIn('setDragImage', start)
        self.assertIn('"trend"', start)
        self.assertIn('.ma-slot__grip', THEME)



    def test_the_grip_stays_inside_the_card_s_own_top_padding(self):
        """The handle is a band over padding, which is the only reason every panel can
        be draggable without one of them knowing. Measured at 3px of clearance, so the
        two numbers are held together here the way CHAT_CHROME is held to .ma-page's:
        shrink .ma-card's padding and the grip starts eating the 마감 card's 7/14/30
        toggle and the 오늘 일정 link, with nothing else saying so.
        """
        card = re.search(r'\.ma-card \{[^}]*padding:(\d+)px', THEME)
        grip = re.search(r'\.ma-slot__grip \{[^}]*height:(\d+)px', THEME)
        self.assertIsNotNone(card)
        self.assertIsNotNone(grip)
        self.assertLess(int(grip.group(1)), int(card.group(1)))


class ThemeCollisionTests(unittest.TestCase):
    """THEME is one stylesheet, and a reused class name loses silently."""

    @staticmethod
    def top_level_rules():
        """(selector, body) for every rule outside a @media / @keyframes block.

        Comments are stripped first, and that is not tidiness. Without it the text
        swept up before a `{` includes whatever comment sits above the rule, so the
        'selector' for a commented rule is a unique string every time and two rules
        with the same class name never compare equal — which is exactly how a second
        `.ma-chip` got past this test and made the header's 수집 멈춤 chip 44px tall
        and brand-blue.
        """
        source = re.sub(r'/\*.*?\*/', '', THEME, flags=re.S)
        rules, selector, body, depth = [], '', '', 0
        for char in source:
            if char == '{':
                depth += 1
                if depth == 1:
                    selector, body = body.strip(), ''
                    continue
            elif char == '}':
                depth -= 1
                if depth == 0:
                    if selector and not selector.startswith('@'):
                        parts = [part.strip() for part in selector.split(',') if part.strip()]
                        for part in parts:
                            # Whether this rule named several selectors at once. A
                            # grouped rule handing one property to a list, then a
                            # narrower rule overriding it for one of them, is the
                            # deliberate pattern; two standalone rules for the same
                            # selector is the accident.
                            rules.append((part, body, len(parts) > 1))
                    body = ''
                    continue
            body += char
        return rules

    def test_no_selector_is_given_display_twice(self):
        """A second `.ma-x { display: … }` wins over the first wherever it sits, and
        nothing says so — `.ma-bars` was `.ma-tally` until it turned out the 할 일 판
        tally had owned that name since before it, and the count rows came out in a
        row because the later flex beat the earlier grid.
        """
        seen = {}
        for selector, body, _ in self.top_level_rules():
            if 'display:' not in body:
                continue
            self.assertNotIn(selector, seen,
                             f'{selector} is given display twice: '
                             f'{seen.get(selector)!r} then {body.strip()!r}')
            seen[selector] = body.strip()

    def test_no_selector_is_given_the_same_property_twice(self):
        """display is the cheapest thing to notice a collision by, not the only one.

        The .ma-chip collision changed background, colour, padding and min-height too,
        and any one of those alone would have been just as invisible. What it is *not*
        is 'no selector twice': a grouped base rule that hands one property to several
        selectors (.ma-kpi__value, .ma-tally__n { font-variant-numeric }) is the
        pattern this file uses on purpose, and it never fights the narrower rule
        beside it because the two are setting different things — and nor is it
        'no property twice': .ma-sheet td, .ma-sheet th sets a colour and .ma-sheet th
        then overrides it, which is the refinement CLAUDE.md names on purpose. What is
        left, and what this asserts, is two *standalone* rules for one selector.
        """
        seen = {}
        for selector, body, grouped in self.top_level_rules():
            if grouped:
                continue
            for declaration in body.split(';'):
                name, _, value = declaration.partition(':')
                name, value = name.strip(), value.strip()
                if not name or not value or name.startswith('--'):
                    continue
                key = (selector, name)
                self.assertNotIn(key, seen,
                                 f'{selector} is given {name} twice: '
                                 f'{seen.get(key)!r} then {value!r}')
                seen[key] = value



class HoverMotionTests(unittest.TestCase):
    """A hover that repaints has to take time doing it.

    Twenty rules changed a background or a colour on :hover with nothing saying how
    long, so the ground under the pointer went grey in one frame and back in one —
    which is a flicker rather than an answer. The two that this cost most are the
    ones a reader's pointer spends the day in: every row of the 메일 목록, and the
    four count rows and two panels of the 대시보드.

    The rule is not 'every rule has a transition'. It is that a selector which
    *changes* on hover must be able to travel: the resting rule it belongs to —
    or a rule that already names the same base, which is how the sidebar's padding
    and the memo's shadow are declared — has to carry a transition.
    """

    REPAINTS = ('background', 'background-color', 'color', 'opacity',
                'border-color', 'box-shadow', 'transform')

    @staticmethod
    def moving(selector):
        """The classes on the element a rule actually repaints — its last compound.

        '.ma-memo:hover .ma-memo__acts' moves .ma-memo__acts, so that is where the
        transition has to be. q-btn--dense and q-btn--rectangle are Quasar's own
        modifiers and never stand without q-btn beside them, so they answer to the
        one .q-btn rule rather than needing one each.
        """
        last = re.sub(r':[a-z-]+(\([^)]*\))?', '', selector).strip().rsplit(' ', 1)[-1]
        names = {re.sub(r'^(q-btn)--.*', r'\1', name)
                 for name in re.findall(r'\.([A-Za-z0-9_-]+)', last)}
        return names or {last}

    def test_every_hover_that_repaints_has_somewhere_to_travel(self):
        resting, states = {}, []
        for selector, body, _ in ThemeCollisionTests.top_level_rules():
            if any(mark in selector for mark in (':hover', ':focus', ':active')):
                states.append((selector, body))
                continue
            if 'transition:' in body:
                resting.setdefault(frozenset(self.moving(selector)), []).append(selector)
        missing = []
        for selector, body in states:
            if ':hover' not in selector and ':focus-within' not in selector:
                continue
            if not any(re.search(rf'(^|;)\s*{name}\s*:', body) for name in self.REPAINTS):
                continue
            names = self.moving(selector)
            if not any(names & set(named) for named in resting):
                missing.append(selector)
        self.assertEqual(missing, [], f'시간 없이 다시 칠해지는 hover: {missing}')


class IconInButtonTests(unittest.TestCase):
    """버튼의 크기가 곧 그 안의 아이콘의 칸이다.

    Quasar 는 아이콘을 버튼 글자의 1.715배로 그리므로, 손대지 않으면 13px 짜리 라벨
    옆에 20px 이 서고 32px 짜리 동그란 버튼은 그 하나로 62%가 찬다. 전수로 재 보면
    이 앱의 버튼은 하나만 빼고 전부 32px 이었으니, '버튼이 크다'는 실은 이 비율이다.
    --ic-* 가 본문 램프와 따로 있는 이유가 여기에 있다.
    """

    def test_the_two_button_sizes_take_the_two_icon_rungs(self):
        self.assertIn('.q-btn .q-icon { font-size:var(--ic-m); }', THEME)
        self.assertIn('.q-btn--dense .q-icon { font-size:var(--ic-s); }', THEME)

    def test_a_button_that_keeps_its_own_transition_outranks_the_base_rule(self):
        """.q-btn 의 transition 은 이 파일에서 이 둘보다 뒤에 있다 — 한 클래스끼리는
        뒤가 이기므로, 나타났다 사라지는 것이 전부인 이 둘은 이름에 .q-btn 을 붙여
        둔다. 붙이지 않으면 opacity 가 계단을 잃고 깜빡인다."""
        for name in ('.q-btn.ma-said__copy', '.q-btn.ma-side__fold'):
            self.assertIn(name, THEME, name)


class SegmentWrapTests(unittest.TestCase):
    """'목록'이 '목/록'이 되면 그것은 글자가 아니라 얼룩이다.

    화면 배치 메뉴의 네 칸짜리 토글이 고정 폭 안에서 줄바꿈되고 있었다. 고친 것이
    더 큰 숫자가 아닌 이유는, 한 칸의 폭을 정하는 것이 글꼴이기 때문이다 — Pretendard
    가 늦게 오면 이 앱은 맑은 고딕으로 한 번 그려지고, 그때 맞는 숫자는 다른 숫자다.
    """

    def test_a_segment_label_never_wraps(self):
        rule = THEME.split('.ma-seg .q-btn {', 1)[1].split('}', 1)[0]
        self.assertIn('white-space:nowrap', rule)

    def test_the_menu_is_as_wide_as_what_is_in_it(self):
        rule = THEME.split('.ma-menu {', 1)[1].split('}', 1)[0]
        self.assertIn('width:max-content', rule)


class DateFieldTests(unittest.TestCase):
    """날짜를 고르는 칸. 브라우저의 type=date 가 있던 자리다.

    그것을 두지 않는 이유는 모양이 아니라 말이다: WebView2 의 그것은 mm/dd/yyyy 로
    묻고 달력은 영어로 열리는데, 이 앱이 날짜를 적는 자리는 전부 2026-09-25 다.
    """

    def test_no_screen_asks_the_browser_for_a_date_widget(self):
        """한 번 돌아오면 그 폼만 다시 다른 앱의 부품을 달고 있게 된다.

        Quasar 의 prop 은 늘 따옴표로 닫히므로 그 모양만 본다 — 왜 쓰지 않는지를
        적어 둔 문장들이 이 파일에 여럿 있고, 그것까지 금지할 일은 아니다.
        """
        source = Path(mail_assistant.webui.__file__).read_text(encoding='utf-8')
        for quote in ("'", '"'):
            for kind in ('date', 'time'):
                self.assertNotIn(f'type={kind}{quote}', source)

    def test_the_week_starts_where_the_calendar_screen_starts_it(self):
        """일정 화면의 FullCalendar 는 firstDay:1 이다. 창 안의 달력만 일요일부터
        시작하면 같은 9월이 두 가지 모양이 된다."""
        self.assertEqual(CAL_LOCALE['firstDayOfWeek'], 1)

    def test_the_day_names_come_from_the_one_list(self):
        """QDate 의 locale 은 일요일부터 세고, core 의 WEEKDAYS 는 월요일부터 센다 —
        그래서 순서를 옮기는 곳이 하나여야 한다."""
        self.assertEqual(CAL_LOCALE['daysShort'], ['일'] + list(WEEKDAYS[:-1]))
        self.assertEqual(CAL_LOCALE['days'][1], '월요일')
        self.assertEqual(CAL_LOCALE['months'][8], '9월')

    def test_the_calendar_carries_its_own_width(self):
        """QDate 의 칸들은 flex row 라 자리가 있는 만큼 벌어진다 — 폭을 주지 않으면
        창 안에서 시트보다 넓은 달력이 열린다."""
        self.assertIn(f'width:{CAL_WIDTH}px', THEME)

    def test_the_hint_is_not_a_date_somebody_could_read_as_a_value(self):
        """자리표시자가 그럴듯한 날짜이면, 빈 칸과 채운 칸을 글자 색으로만 구별하게
        된다."""
        self.assertNotRegex(DATE_HINT, r'\d')
        self.assertNotRegex(TIME_HINT, r'\d')


class PickerFocusTests(unittest.TestCase):
    """메일 붙이기 피커가 글자 하나마다 제 입력칸을 다시 만들고 있었다.

    panel.refresh() 가 칸과 찾은 줄을 한꺼번에 지웠고, 새로 만들어진 칸에는 캐럿이
    없다 — 한 글자 적을 때마다 포커스가 빠지는 칸이었다. 고친 것은 둘이다: 찾은 줄만
    따로 다시 그리고, 서버가 그 말을 듣는 것을 손이 멈출 때까지 미룬다.
    """

    def test_the_search_box_waits_for_the_hand_to_stop(self):
        source = inspect.getsource(mail_assistant.webui.mail_picker)
        self.assertIn('debounce={PICK_WAIT}', source)
        self.assertGreater(PICK_WAIT, 0)

    def test_typing_never_rebuilds_the_box_it_is_typed_into(self):
        """look() 이 panel 을 새로 그리면 그 안에 이 칸이 있다."""
        look = inspect.getsource(mail_assistant.webui.mail_picker) \
            .split('def look(', 1)[1].split('\n\n', 1)[0]
        self.assertIn('hits.refresh()', look)
        self.assertNotIn('panel.refresh()', look)


if __name__ == '__main__':
    unittest.main()
