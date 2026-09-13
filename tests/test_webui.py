"""The web 현황 screen's shaping, which needs no nicegui and so runs on every platform."""
import datetime
import socket
import tempfile
import unittest
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

from mail_assistant.core import HANDLED, PROGRESS, Store, account_key
from mail_assistant.dashboard import PRIORITIES
from mail_assistant.hub import Hub
from mail_assistant.overview import DUE_DAYS, overview
from mail_assistant.webui import (CARD_TONES, DEFAULT_LIST, STATUS, bar_rows, deadline_rows,
                                  detail_view, href, list_state, listing, new_token, open_port,
                                  release_store, run_summary, snapshot, summary_line, tidy_body,
                                  calendar_events, card_target, countdown_text, run_view,
                                  board, chat_context, draft_view, label_step, stats_view,
                                  vendor_path)

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


class PaletteTests(unittest.TestCase):
    """The palette is keyed by name, so a renamed level would silently lose its colour."""

    def test_every_priority_has_a_status_colour(self):
        self.assertEqual(set(STATUS), {name for name, _ in PRIORITIES})

    def test_card_tones_name_cards_that_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            cards = snapshot(Path(folder), {}, TODAY)['cards']
        self.assertLessEqual(set(CARD_TONES), set(cards))
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
