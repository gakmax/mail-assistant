import datetime
import json
import poplib
import sqlite3
import time
import subprocess
import sys
import tempfile
import unittest
import types
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import call, patch, MagicMock

from mail_assistant.core import (ANALYZING, FAILED, HANDLED, HEADERS, PRIORITY_ORDER, ROOM_MARK, Store,
                                 account_key, STATES, STATE_SQL, day_bounds, filter_rows,
                                 local_text, parse_mail, row_view, sql_text, state_of,
                                 workbook_rows)
from mail_assistant.excel import (Excel, ExcelUpdateError, append_missing, ensure_table,
                                  error_detail, first_column, repair_generated_header,
                                  row_text)
from mail_assistant.calendar_sheet import (SHEET, Entry, cell_text, collect, month_grid,
                                           next_month, overdue, parse_day, sheet_events,
                                           signature, update_calendar)
from mail_assistant.dashboard import (PRIORITIES, SHEET as DASHBOARD, UPCOMING_ROWS,
                                      UPCOMING_TOP, blocks, describe, update_dashboard, upcoming)
from mail_assistant.excel import address_of, link_to_draft, link_to_mail, mail_rows, mailto
from mail_assistant.overview import overview, past_due
from mail_assistant.settings import (CUSTOM, field_errors, model_choices, model_rows,
                                     model_value, normalize, read_models)
from mail_assistant.services import check_connection, connection_steps, login_state
from mail_assistant.style import STYLE_VERSION, URGENT, apply_style
from mail_assistant.services import (analyze, chat_reply, chat_schema, fetch_mail,
                                      read_password, save_password)


CONFIG = {'host': 'pop3s.hiworks.com', 'port': 995, 'email': 'test@example.com', 'model': ''}
RESULT = {'category': '업무 요청', 'summary': '견적 회신 요청', 'requests': '견적 확인',
          'events': [{'title': '회신', 'start': '', 'deadline': '2026-09-11', 'evidence': '9월 11일까지', 'needs_review': False}],
          'priority': '높음', 'priority_reason': '회신 마감 명시', 'next_action': '견적 검토',
          'reply_needed': True, 'reply_subject': 'Re: 견적', 'reply_draft': '확인 후 회신드리겠습니다.'}


def mail():
    msg = EmailMessage()
    msg['Subject'] = '견적 요청'
    msg['From'] = 'sender@example.com'
    msg['Date'] = 'Thu, 10 Sep 2026 10:00:00 +0900'
    msg.set_content('9월 11일까지 회신 부탁드립니다.')
    return msg.as_bytes()


class FakePOP:
    messages = {'old': mail()}
    def __init__(self, *args, **kwargs):
        self.deleted = False
    def user(self, user): pass
    def pass_(self, password): pass
    def quit(self): pass
    def uidl(self):
        return b'+OK', [f'{i} {uid}'.encode() for i, uid in enumerate(self.messages, 1)], 0
    def retr(self, number):
        return b'+OK', list(self.messages.values())[number - 1].splitlines(), 0
    def dele(self, number):
        raise AssertionError('mail must never be deleted')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'mail.db')
        FakePOP.messages = {'old': mail()}

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_baseline_new_mail_and_restart_dedup(self):
        fetch_mail(CONFIG, self.store, 'secret', FakePOP)
        self.assertEqual(self.store.counts(account_key(CONFIG))['total'], 0)
        FakePOP.messages['new'] = mail()
        fetch_mail(CONFIG, self.store, 'secret', FakePOP)
        self.store.db.close()
        self.store = Store(Path(self.temp.name) / 'mail.db')
        fetch_mail(CONFIG, self.store, 'secret', FakePOP)
        self.assertEqual(self.store.counts(account_key(CONFIG))['total'], 1)

    def test_empty_mailbox_baseline_and_account_isolation(self):
        FakePOP.messages = {}
        fetch_mail(CONFIG, self.store, 'secret', FakePOP)
        FakePOP.messages = {'first': mail()}
        fetch_mail(CONFIG, self.store, 'secret', FakePOP)
        other = {**CONFIG, 'email': 'other@example.com'}
        fetch_mail(other, self.store, 'secret', FakePOP)
        self.assertEqual(self.store.counts(account_key(CONFIG))['total'], 1)
        self.assertEqual(self.store.counts(account_key(other))['total'], 0)

    def test_retr_failure_is_not_marked_seen(self):
        fetch_mail(CONFIG, self.store, 'secret', FakePOP)
        FakePOP.messages['new'] = mail()
        with patch.object(FakePOP, 'retr', side_effect=OSError):
            with self.assertRaises(OSError):
                fetch_mail(CONFIG, self.store, 'secret', FakePOP)
        self.assertNotIn('new', self.store.seen(account_key(CONFIG)))

    def test_result_survives_failed_export(self):
        ident = self.store.add(account_key(CONFIG), 'new', mail())
        self.store.analyzed(ident, parse_mail(mail()), RESULT)
        rows = self.store.unexported(account_key(CONFIG))
        self.assertEqual(len(rows), 1)
        self.assertEqual(workbook_rows(rows[0])['일정'][0][0], ident + ':0')
        self.store.exported([ident])
        self.assertEqual(len(self.store.unexported(account_key(CONFIG))), 0)

    def test_backoff(self):
        ident = self.store.add(account_key(CONFIG), 'new', mail())
        self.store.failed(ident, 'error', 100)
        self.assertEqual(len(self.store.pending(account_key(CONFIG), 99)), 0)
        self.assertEqual(len(self.store.pending(account_key(CONFIG), 100)), 1)

    def test_html_and_attachments(self):
        msg = EmailMessage()
        msg['Subject'] = '한글 제목'
        msg.set_content('<p>본문</p><script>숨김</script>', subtype='html')
        msg.add_attachment(b'abc', maintype='application', subtype='pdf', filename='견적.pdf')
        data = parse_mail(msg.as_bytes())
        self.assertIn('본문', data['body'])
        self.assertNotIn('숨김', data['body'])
        self.assertEqual(data['attachments'], ['견적.pdf'])

    def test_codex_stdin_schema_and_no_api_key(self):
        def execute(command, **kwargs):
            self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
            self.assertNotIn('CODEX_API_KEY', kwargs['env'])
            self.assertNotIn('9월 11일', ' '.join(command))
            self.assertIn('9월 11일', kwargs['input'])
            self.assertIn('--ignore-user-config', command)
            Path(command[command.index('-o') + 1]).write_text(json.dumps(RESULT), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0)
        ident = self.store.add(account_key(CONFIG), 'new', mail())
        row = self.store.pending(account_key(CONFIG), 0)[0]
        with patch('mail_assistant.services.codex_command', return_value=['codex']), patch('mail_assistant.services.subprocess.run', side_effect=execute), patch.dict('os.environ', {'OPENAI_API_KEY': 'secret', 'CODEX_API_KEY': 'secret'}):
            _, result = analyze(row, CONFIG)
        self.assertEqual(result, RESULT)

    def test_invalid_codex_output_not_accepted(self):
        def execute(command, **kwargs):
            Path(command[command.index('-o') + 1]).write_text('{}', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0)
        self.store.add(account_key(CONFIG), 'new', mail())
        row = self.store.pending(account_key(CONFIG), 0)[0]
        from jsonschema import ValidationError
        with patch('mail_assistant.services.codex_command', return_value=['codex']), patch('mail_assistant.services.subprocess.run', side_effect=execute):
            with self.assertRaises(ValidationError):
                analyze(row, CONFIG)


OLD_SCHEMA = '''
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE seen (account TEXT, uid TEXT, PRIMARY KEY(account,uid));
    CREATE TABLE mail (
        id TEXT PRIMARY KEY, account TEXT NOT NULL, uid TEXT NOT NULL,
        raw BLOB NOT NULL, received TEXT NOT NULL, parsed TEXT,
        result TEXT, exported INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', UNIQUE(account,uid));
'''


class MigrationTests(unittest.TestCase):
    def old_database(self, folder):
        """A database written by the installed version, with one analysed mail."""
        path = Path(folder) / 'mail.db'
        db = sqlite3.connect(path)
        db.executescript(OLD_SCHEMA)
        db.execute('INSERT INTO mail(id,account,uid,raw,received,parsed,result,exported) VALUES (?,?,?,?,?,?,?,1)',
                   ('old-1', 'acct', 'uid-1', mail(), '2026-09-10T01:00:00+00:00',
                    json.dumps({'sender': 'sender@example.com', 'subject': '견적 요청', 'attachments': []}),
                    json.dumps(RESULT)))
        db.execute("INSERT INTO meta VALUES ('baseline:acct', '2026-09-10T00:00:00+00:00')")
        db.commit()
        db.close()
        return path

    def test_rows_survive_and_new_columns_appear(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.old_database(folder)
            store = Store(path)
            columns = {row['name'] for row in store.db.execute('PRAGMA table_info(mail)')}
            self.assertLessEqual({'handled', 'draft_edit', 'notified', 'analyzed_at', 'exported_at',
                                  'subject', 'sender', 'analyzing', 'todo_hidden'}, columns)
            row = store.detail('old-1')
            self.assertEqual(row['handled'], '')
            self.assertEqual(store.get_meta('baseline:acct'), '2026-09-10T00:00:00+00:00')
            # subject/sender are backfilled from the analysis that was already stored
            self.assertEqual((row['subject'], row['sender']), ('견적 요청', 'sender@example.com'))
            store.db.close()

    def test_opening_twice_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.old_database(folder)
            Store(path).db.close()
            store = Store(path)
            self.assertEqual(store.counts('acct')['total'], 1)
            store.db.close()


class StoreViewTests(unittest.TestCase):
    def store(self, folder):
        store = Store(Path(folder) / 'mail.db')
        first = store.add('acct', 'uid-1', mail())
        second = store.add('acct', 'uid-2', mail())
        store.analyzed(first, {'sender': 'kim@example.com', 'subject': '견적 요청', 'attachments': []}, RESULT)
        return store, first, second

    def test_add_stores_the_headers_for_the_list(self):
        with tempfile.TemporaryDirectory() as folder:
            store, first, _ = self.store(folder)
            view = row_view(store.page('acct')[-1])
            self.assertEqual(view['subject'], '견적 요청')
            self.assertEqual(view['sender'], 'sender@example.com')
            store.db.close()

    def test_state_reflects_analysis_and_handling(self):
        with tempfile.TemporaryDirectory() as folder:
            store, first, second = self.store(folder)
            states = {row['id']: state_of(row) for row in store.page('acct')}
            self.assertEqual(states[first], '미처리')
            self.assertEqual(states[second], '분석 대기')
            store.failed(second, '분석 실패', 0)
            self.assertEqual(state_of(store.detail(second)), '1회 실패')
            store.set_handled(first, HANDLED)
            self.assertEqual(state_of(store.detail(first)), HANDLED)
            self.assertEqual(store.open_count('acct'), 0)
            store.db.close()

    def test_filter_by_text_and_state(self):
        views = [{'subject': '견적 요청', 'sender': 'kim@a.com', 'state': '미처리'},
                 {'subject': 'Weekly report', 'sender': 'admin@b.com', 'state': '처리'}]
        self.assertEqual(len(filter_rows(views, query='견적')), 1)
        self.assertEqual(len(filter_rows(views, query='WEEKLY')), 1)      # case insensitive
        self.assertEqual(len(filter_rows(views, query='@b.com')), 1)      # sender too
        self.assertEqual(len(filter_rows(views, state='처리')), 1)
        self.assertEqual(len(filter_rows(views, query='견적', state='처리')), 0)
        self.assertEqual(len(filter_rows(views)), 2)

    def test_filter_reaches_failed_mail_whatever_the_retry_count(self):
        views = [{'subject': 'a', 'sender': 'a@a', 'state': '1회 실패'},
                 {'subject': 'b', 'sender': 'b@b', 'state': '4회 실패'},
                 {'subject': 'c', 'sender': 'c@c', 'state': '분석 대기'}]
        self.assertEqual([view['subject'] for view in filter_rows(views, state=FAILED)], ['a', 'b'])
        self.assertEqual([view['subject'] for view in filter_rows(views, state='분석 대기')], ['c'])
        self.assertEqual(len(filter_rows(views, state='없는 상태')), 0)

    def test_reset_clears_backoff_and_optionally_the_result(self):
        with tempfile.TemporaryDirectory() as folder:
            store, first, second = self.store(folder)
            store.failed(second, '분석 실패', time.time() + 3600)
            store.reset([second])
            row = store.detail(second)
            self.assertEqual((row['attempts'], row['retry_at'], row['error']), (0, 0, ''))
            self.assertEqual([r['id'] for r in store.pending('acct', time.time())], [second])
            store.reset([first], reanalyze=True)
            self.assertIsNone(store.detail(first)['result'])
            self.assertEqual({r['id'] for r in store.retryable('acct')}, {first, second})
            store.db.close()

    def test_draft_edit_and_notified_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            store, first, _ = self.store(folder)
            store.set_draft(first, '사람이 고친 초안')
            self.assertEqual(store.detail(first)['draft_edit'], '사람이 고친 초안')
            self.assertEqual([r['id'] for r in store.unnotified('acct')], [first])
            store.mark_notified([first])
            self.assertEqual(store.unnotified('acct'), [])
            store.db.close()


class SearchTests(unittest.TestCase):
    """The list query. Filtering, sorting and paging all happen in SQL now."""

    ACCOUNT = 'acct'

    def store(self, folder):
        store = Store(Path(folder) / 'mail.db')
        plan = [
            # uid, subject, sender, body, category, priority, analysed, handled, attempts
            ('uid-1', '견적 검토 요청', 'kim@buyer.example', '9월 견적서를 검토해 주세요',
             '견적·계약', '긴급', True, '', 0),
            ('uid-2', 'Weekly report', 'admin@corp.example', '주간 보고 첨부합니다',
             '공지', '낮음', True, HANDLED, 0),
            ('uid-3', '회의 일정 조정', 'lee@corp.example', '수요일로 옮길까요',
             '회의·일정', '보통', True, '', 0),
            ('uid-4', '아직 분석 안 됨', 'new@corp.example', '본문', '', '', False, '', 0),
            ('uid-5', '분석 실패한 메일', 'bad@corp.example', '본문', '', '', False, '', 3),
        ]
        self.ids = {}
        for uid, subject, sender, body, category, priority, analysed, handled, attempts in plan:
            message = EmailMessage()
            message['From'] = sender
            message['Subject'] = subject
            message['Date'] = 'Fri, 11 Sep 2026 10:00:00 +0900'
            message.set_content(body)
            ident = store.add(self.ACCOUNT, uid, message.as_bytes())
            self.ids[uid] = ident
            if analysed:
                store.analyzed(ident, {'sender': sender, 'subject': subject, 'body': body,
                                       'attachments': []},
                               {'category': category, 'summary': f'{subject} 요약',
                                'requests': '회신 필요', 'events': [],
                                'priority': priority, 'priority_reason': '근거',
                                'next_action': '담당자 확인', 'reply_needed': False,
                                'reply_subject': '', 'reply_draft': ''})
            if handled:
                store.set_handled(ident, handled)
            if attempts:
                store.failed(ident, '분석 실패', 0)
                for _ in range(attempts - 1):
                    store.failed(ident, '분석 실패', 0)
        return store

    def subjects(self, store, **kwargs):
        rows, _ = store.search(self.ACCOUNT, **kwargs)
        return [row['subject'] for row in rows]

    def test_text_matches_subject_sender_body_and_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            self.assertEqual(self.subjects(store, query='견적 검토'), ['견적 검토 요청'])
            self.assertEqual(self.subjects(store, query='WEEKLY'), ['Weekly report'])   # ascii case
            self.assertEqual(self.subjects(store, query='@buyer.example'), ['견적 검토 요청'])
            self.assertEqual(self.subjects(store, query='수요일로'), ['회의 일정 조정'])   # body
            self.assertEqual(self.subjects(store, query='회신 필요'),
                             ['회의 일정 조정', 'Weekly report', '견적 검토 요청'])         # requests
            store.db.close()

    def test_mail_stored_in_the_same_tick_still_has_one_order(self):
        """`received` comes from now(), and a Windows clock ticks about every 15ms.

        A cycle that stores several mails therefore writes the same string for all of
        them, and an ORDER BY with no tie-break may hand back a different order every
        call — which is how the list reshuffled itself between two refreshes.
        """
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.db.execute("UPDATE mail SET received='2026-09-11T01:00:00+00:00'")
            store.db.commit()
            first = self.subjects(store, query='회신 필요')
            self.assertEqual(first, ['회의 일정 조정', 'Weekly report', '견적 검토 요청'])
            for _ in range(5):
                self.assertEqual(self.subjects(store, query='회신 필요'), first)
            self.assertEqual([row['subject'] for row in store.page(self.ACCOUNT)][:3],
                             ['분석 실패한 메일', '아직 분석 안 됨', '회의 일정 조정'])
            store.db.close()

    def test_each_state_filter_returns_its_own_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            self.assertEqual(self.subjects(store, state=HANDLED), ['Weekly report'])
            self.assertEqual(self.subjects(store, state='미처리'),
                             ['회의 일정 조정', '견적 검토 요청'])
            self.assertEqual(self.subjects(store, state='분석 대기'), ['아직 분석 안 됨'])
            self.assertEqual(self.subjects(store, state=FAILED), ['분석 실패한 메일'])
            self.assertEqual(len(self.subjects(store, state='없는 상태')), 5)   # ignored
            store.db.close()

    def test_failures_are_reachable_which_they_were_not_before(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            rows, total = store.search(self.ACCOUNT, state=FAILED)
            self.assertEqual(total, 1)
            self.assertEqual(state_of(rows[0]), '3회 실패')
            store.db.close()

    def test_priority_sorts_by_urgency_not_by_spelling(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            rows, _ = store.search(self.ACCOUNT, state='미처리', sort='priority', desc=False)
            self.assertEqual([row['subject'] for row in rows],
                             ['견적 검토 요청', '회의 일정 조정'])          # 긴급 then 보통
            store.db.close()

    def test_state_sorts_by_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            rows, _ = store.search(self.ACCOUNT, sort='state', desc=False)
            self.assertEqual(state_of(rows[0]), '분석 대기')
            self.assertEqual(state_of(rows[-1]), HANDLED)
            store.db.close()

    def test_sort_by_subject_and_an_unknown_key_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            self.assertEqual(self.subjects(store, sort='subject', desc=False)[0], 'Weekly report')
            newest = self.subjects(store)
            self.assertEqual(self.subjects(store, sort='없는 컬럼'), newest)
            store.db.close()

    def test_paging_reports_the_total_not_the_page(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            first, total = store.search(self.ACCOUNT, limit=2, offset=0)
            second, again = store.search(self.ACCOUNT, limit=2, offset=2)
            self.assertEqual((total, again), (5, 5))
            self.assertEqual(len(first), 2)
            self.assertEqual(len(second), 2)
            self.assertFalse({row['id'] for row in first} & {row['id'] for row in second})
            store.db.close()

    def test_text_and_state_narrow_together(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            self.assertEqual(self.subjects(store, query='견적', state=HANDLED), [])
            self.assertEqual(self.subjects(store, query='견적', state='미처리'),
                             ['견적 검토 요청'])
            store.db.close()

    def test_the_verdict_columns_track_the_result(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            row = store.detail(self.ids['uid-1'])
            self.assertEqual((row['category'], row['priority']), ('견적·계약', '긴급'))
            store.reset([self.ids['uid-1']], reanalyze=True)
            row = store.detail(self.ids['uid-1'])
            self.assertEqual((row['category'], row['priority']), ('', ''))
            store.db.close()

    def test_migrate_backfills_the_verdicts_of_older_mail(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.db.execute("UPDATE mail SET category='', priority=''")
            store.db.commit()
            store.db.close()
            again = Store(Path(folder) / 'mail.db')          # migrate() runs on open
            row = again.detail(self.ids['uid-1'])
            self.assertEqual((row['category'], row['priority']), ('견적·계약', '긴급'))
            again.db.close()

    def test_a_broken_result_does_not_stop_the_backfill(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.db.execute("UPDATE mail SET category='', priority=''")
            store.db.execute("UPDATE mail SET result='{망가진' WHERE uid='uid-2'")
            store.db.commit()
            store.db.close()
            again = Store(Path(folder) / 'mail.db')
            self.assertEqual(again.detail(self.ids['uid-1'])['priority'], '긴급')
            self.assertEqual(again.detail(self.ids['uid-2'])['priority'], '')
            again.db.close()

    def test_handling_many_at_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.set_handled_many([self.ids['uid-1'], self.ids['uid-3']], HANDLED)
            self.assertEqual(store.search(self.ACCOUNT, state=HANDLED)[1], 3)
            store.set_handled_many([self.ids['uid-1']], '')
            self.assertEqual(store.search(self.ACCOUNT, state=HANDLED)[1], 2)
            store.db.close()

    def test_deleting_mail_takes_its_chat_and_leaves_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.add_chat(self.ACCOUNT, 'user', '이 건 어떻게 할까요', self.ids['uid-1'])
            store.add_chat(self.ACCOUNT, 'user', '일반 상담')
            store.delete([self.ids['uid-1'], self.ids['uid-4']])
            self.assertEqual(self.subjects(store), ['분석 실패한 메일', '회의 일정 조정',
                                                    'Weekly report'])
            self.assertEqual(store.chat(self.ACCOUNT, self.ids['uid-1']), [])
            self.assertEqual(len(store.chat(self.ACCOUNT)), 1)
            store.db.close()

    def test_a_thread_is_listed_for_every_room_that_has_one(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.add_chat(self.ACCOUNT, 'user', '이 건 어떻게 할까요', self.ids['uid-1'])
            store.add_chat(self.ACCOUNT, 'codex', '이렇게 하세요', self.ids['uid-1'])
            store.add_chat(self.ACCOUNT, 'user', '일반 상담')
            rooms = {row['key']: row for row in store.rooms(self.ACCOUNT)}
            self.assertEqual(set(rooms), {'', self.ids['uid-1']})
            self.assertEqual(rooms[self.ids['uid-1']]['turns'], 2)
            # The mail thread is named after the mail, in one query for all of them.
            self.assertEqual(rooms[self.ids['uid-1']]['name'], '견적 검토 요청')
            self.assertEqual(rooms[self.ids['uid-1']]['role'], 'codex')
            self.assertEqual(rooms[self.ids['uid-1']]['last'], '이렇게 하세요')
            store.db.close()

    def test_the_general_thread_is_listed_even_when_it_is_empty(self):
        """It is where the page lands when nothing else is asked for."""
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            self.assertEqual([row['key'] for row in store.rooms(self.ACCOUNT)], [''])
            store.db.close()

    def test_rooms_come_back_newest_activity_first(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.add_chat(self.ACCOUNT, 'user', '먼저', self.ids['uid-1'])
            store.add_chat(self.ACCOUNT, 'user', '나중', self.ids['uid-2'])
            self.assertEqual([row['key'] for row in store.rooms(self.ACCOUNT)][:2],
                             [self.ids['uid-2'], self.ids['uid-1']])
            store.db.close()

    def test_a_free_room_shows_up_before_anyone_has_written_in_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            ident = store.new_room(self.ACCOUNT)
            rooms = {row['key']: row for row in store.rooms(self.ACCOUNT)}
            self.assertIn(ident, rooms)
            self.assertEqual((rooms[ident]['turns'], rooms[ident]['name']), (0, ''))
            store.db.close()

    def test_a_room_names_itself_once_and_a_rename_always_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            ident = store.new_room(self.ACCOUNT)
            store.name_room(ident, '첫 질문', only_if_unnamed=True)
            # A second question must not rename the room out from under the first.
            store.name_room(ident, '둘째 질문', only_if_unnamed=True)
            rooms = {row['key']: row for row in store.rooms(self.ACCOUNT)}
            self.assertEqual(rooms[ident]['name'], '첫 질문')
            store.name_room(ident, '손으로 바꾼 이름')
            rooms = {row['key']: row for row in store.rooms(self.ACCOUNT)}
            self.assertEqual(rooms[ident]['name'], '손으로 바꾼 이름')
            store.db.close()

    def test_a_free_room_id_can_never_be_a_mail_id(self):
        """Mail ids are 24 hex characters, so the marker is what tells the two apart."""
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            ident = store.new_room(self.ACCOUNT)
            self.assertTrue(ident.startswith(ROOM_MARK))
            self.assertNotIn(ident, self.ids.values())
            store.db.close()

    def test_dropping_a_free_room_takes_its_turns_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            ident = store.new_room(self.ACCOUNT)
            store.add_chat(self.ACCOUNT, 'user', '자유 질문', ident)
            store.add_chat(self.ACCOUNT, 'user', '메일 질문', self.ids['uid-1'])
            self.assertTrue(store.drop_room(self.ACCOUNT, ident))
            self.assertEqual(store.chat(self.ACCOUNT, ident), [])
            self.assertEqual(len(store.chat(self.ACCOUNT, self.ids['uid-1'])), 1)
            self.assertNotIn(ident, [row['key'] for row in store.rooms(self.ACCOUNT)])
            store.db.close()

    def test_drop_room_refuses_a_mail_thread(self):
        """A mail's thread goes with the mail; this button must not be able to reach it."""
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.add_chat(self.ACCOUNT, 'user', '메일 질문', self.ids['uid-1'])
            self.assertFalse(store.drop_room(self.ACCOUNT, self.ids['uid-1']))
            self.assertFalse(store.drop_room(self.ACCOUNT, ''))
            self.assertEqual(len(store.chat(self.ACCOUNT, self.ids['uid-1'])), 1)
            store.db.close()

    def test_deleted_mail_is_not_collected_again(self):
        """`seen` keeps the uid: the mail is still on the server, and the user threw
        away the copy on purpose."""
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.delete([self.ids['uid-1']])
            self.assertIn('uid-1', store.seen(self.ACCOUNT))
            store.db.close()

    def test_the_priority_order_matches_the_dashboard(self):
        """Two lists of the same four words; a rename must not silently reorder one."""
        self.assertEqual(PRIORITY_ORDER, tuple(name for name, _ in PRIORITIES))

    def test_the_offered_filters_are_exactly_the_ones_sql_knows(self):
        """A value in the dropdown with no SQL behind it would silently show everything."""
        self.assertEqual(set(STATES) - {''}, set(STATE_SQL))
        self.assertEqual(STATES[0], '')

    def test_a_constant_with_a_quote_is_refused_rather_than_interpolated(self):
        self.assertEqual(sql_text('처리'), "'처리'")
        with self.assertRaises(ValueError):
            sql_text("' OR 1=1 --")


class AnalyzingTests(unittest.TestCase):
    """'분석 중': what the worker is holding right now, as a state the screens can read."""

    ACCOUNT = 'acct'

    def store(self, folder):
        store = Store(Path(folder) / 'mail.db')
        self.ids = {uid: store.add(self.ACCOUNT, uid, mail()) for uid in ('uid-1', 'uid-2')}
        return store

    def test_a_mail_in_codex_says_so_instead_of_waiting(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            self.assertEqual(state_of(store.detail(self.ids['uid-1'])), '분석 대기')
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            self.assertEqual(state_of(store.detail(self.ids['uid-1'])), ANALYZING)
            self.assertEqual(state_of(store.detail(self.ids['uid-2'])), '분석 대기')
            store.db.close()

    def test_a_retry_in_flight_is_being_analysed_not_failing(self):
        """'2회 실패' of a mail Codex is reading right now is a week-old fact."""
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.failed(self.ids['uid-1'], '분석 실패', 0)
            store.failed(self.ids['uid-1'], '분석 실패', 0)
            self.assertEqual(state_of(store.detail(self.ids['uid-1'])), '2회 실패')
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            self.assertEqual(state_of(store.detail(self.ids['uid-1'])), ANALYZING)
            store.db.close()

    def test_only_one_mail_carries_the_marker(self):
        """One Codex process at a time, so a second mark clears the first."""
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-2'])
            self.assertEqual(store.analyzing(self.ACCOUNT), self.ids['uid-2'])
            self.assertEqual(state_of(store.detail(self.ids['uid-1'])), '분석 대기')
            store.mark_analyzing(self.ACCOUNT, '')
            self.assertEqual(store.analyzing(self.ACCOUNT), '')
            store.db.close()

    def test_the_filter_finds_it_and_the_other_two_do_not_claim_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.failed(self.ids['uid-1'], '분석 실패', 0)
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            for state, expected in ((ANALYZING, [self.ids['uid-1']]),
                                    (FAILED, []),
                                    ('분석 대기', [self.ids['uid-2']])):
                rows, total = store.search(self.ACCOUNT, state=state)
                self.assertEqual([row['id'] for row in rows], expected, state)
                self.assertEqual(total, len(expected), state)
            store.db.close()

    def test_an_analysed_mail_drops_the_marker_from_every_state(self):
        """analyzed() does not clear it; the worker's finally does. The state still
        reads from the result, because a mail with an answer is not being analysed."""
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            store.analyzed(self.ids['uid-1'], {'sender': 'a@b.c', 'subject': 's',
                                               'attachments': []}, RESULT)
            self.assertEqual(state_of(store.detail(self.ids['uid-1'])), '미처리')
            rows, _ = store.search(self.ACCOUNT, state=ANALYZING)
            self.assertEqual(rows, [])
            store.db.close()

    def test_a_mail_in_codex_is_not_reset_and_the_count_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.analyzed(self.ids['uid-1'], {'sender': 'a@b.c', 'subject': 's',
                                               'attachments': []}, RESULT)
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            self.assertEqual(store.reset([self.ids['uid-1']], reanalyze=True), 0)
            self.assertIsNotNone(store.detail(self.ids['uid-1'])['result'])
            store.mark_analyzing(self.ACCOUNT, '')
            self.assertEqual(store.reset([self.ids['uid-1']], reanalyze=True), 1)
            self.assertIsNone(store.detail(self.ids['uid-1'])['result'])
            store.db.close()

    def test_a_mixed_selection_resets_what_it_can(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            self.assertEqual(store.reset(list(self.ids.values())), 1)
            store.db.close()

    def test_sorting_by_state_puts_it_between_waiting_and_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.store(folder)
            store.failed(self.ids['uid-2'], '분석 실패', 0)
            store.mark_analyzing(self.ACCOUNT, self.ids['uid-1'])
            rows, _ = store.search(self.ACCOUNT, sort='state', desc=False)
            self.assertEqual([state_of(row) for row in rows], [ANALYZING, '1회 실패'])
            store.db.close()


class BoardHiddenTests(unittest.TestCase):
    """A mail card swept off the 할 일 판. The mail itself is untouched."""

    def test_the_flag_is_the_mails_own_and_survives_a_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mail.db'
            store = Store(path)
            ident = store.add('acct', 'uid-1', mail())
            self.assertEqual(store.detail(ident)['todo_hidden'], 0)
            store.set_todo_hidden(ident)
            store.db.close()
            store = Store(path)
            self.assertEqual(store.detail(ident)['todo_hidden'], 1)
            self.assertEqual([row['todo_hidden'] for row in store.page('acct')], [1])
            store.set_todo_hidden(ident, False)
            self.assertEqual(store.detail(ident)['todo_hidden'], 0)
            store.db.close()


class NoteStoreTests(unittest.TestCase):
    """메모. The table is new, so nothing here is a migration — only the rules."""

    @contextmanager
    def opened(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            try:
                yield store
            finally:
                store.db.close()

    def test_pinned_first_then_most_recently_written(self):
        with self.opened() as store:
            first = store.add_note('acct', '하나')
            second = store.add_note('acct', '둘')
            third = store.add_note('acct', '셋')
            store.set_note_pinned(first)
            # Every id in one burst shares a `now()` string, so the order here is
            # decided by the id tie-break and not by whatever sqlite felt like.
            self.assertEqual([row['id'] for row in store.notes('acct')],
                             [first, third, second])

    def test_only_the_text_moves_a_memo_to_the_front(self):
        with self.opened() as store:
            first = store.add_note('acct', '하나')
            second = store.add_note('acct', '둘')
            store.set_note_color(first, 'green')
            self.assertEqual([row['id'] for row in store.notes('acct')], [second, first])
            store.set_note_text(first, '하나 고침')
            self.assertEqual([row['id'] for row in store.notes('acct')][0], first)

    def test_a_mail_filter_narrows_to_that_mails_own(self):
        with self.opened() as store:
            ident = store.add('acct', 'uid-1', mail())
            attached = store.add_note('acct', '붙임', mail_id=ident)
            store.add_note('acct', '자유')
            self.assertEqual([row['id'] for row in store.notes('acct', ident)], [attached])
            self.assertEqual([row['id'] for row in store.notes('acct', '')],
                             [row['id'] for row in store.notes('acct')
                              if not row['mail_id']])

    def test_deleting_a_mail_keeps_the_memo_and_cuts_it_loose(self):
        """The analysis and the draft came from the mail; a memo is the user's own."""
        with self.opened() as store:
            ident = store.add('acct', 'uid-1', mail())
            note = store.add_note('acct', '박 과장님 견적 건', mail_id=ident)
            store.delete([ident])
            rows = list(store.notes('acct'))
            self.assertEqual([row['id'] for row in rows], [note])
            self.assertEqual(rows[0]['text'], '박 과장님 견적 건')
            self.assertEqual(rows[0]['mail_id'], '')

    def test_a_blank_memo_is_swept_and_a_written_one_is_not(self):
        with self.opened() as store:
            blank = store.add_note('acct')
            spaces = store.add_note('acct', '   \n ')
            written = store.add_note('acct', '적은 것')
            store.delete_empty_notes('acct')
            self.assertEqual([row['id'] for row in store.notes('acct')], [written])
            self.assertNotIn(blank, [row['id'] for row in store.notes('acct')])
            self.assertNotIn(spaces, [row['id'] for row in store.notes('acct')])

    def test_the_sweep_spares_the_one_just_made(self):
        with self.opened() as store:
            store.add_note('acct')
            keep = store.add_note('acct')
            store.delete_empty_notes('acct', keep=keep)
            self.assertEqual([row['id'] for row in store.notes('acct')], [keep])

    def test_the_sweep_stops_at_the_account(self):
        with self.opened() as store:
            mine = store.add_note('acct')
            theirs = store.add_note('other')
            store.delete_empty_notes('acct')
            self.assertEqual([row['id'] for row in store.notes('acct')], [])
            self.assertEqual([row['id'] for row in store.notes('other')], [theirs])
            self.assertNotEqual(mine, theirs)

    def test_subjects_arrive_in_one_query_and_skip_what_is_gone(self):
        with self.opened() as store:
            ident = store.add('acct', 'uid-1', mail())
            self.assertEqual(store.mail_subjects([ident, 'gone', '', ident]),
                             {ident: '견적 요청'})
            self.assertEqual(store.mail_subjects([]), {})


class DayWindowTests(unittest.TestCase):
    def test_day_bounds_are_korean_midnight_in_utc(self):
        start, end = day_bounds(datetime.date(2026, 9, 11))
        self.assertEqual(start, '2026-09-10T15:00:00+00:00')
        self.assertEqual(end, '2026-09-11T15:00:00+00:00')

    def test_local_text_shifts_to_korean_time(self):
        self.assertEqual(local_text('2026-09-10T15:30:00+00:00'), '09-11 00:30')
        self.assertEqual(local_text(''), '')
        self.assertEqual(local_text('망가진 값'), '망가진 값')

    def test_day_counts_use_the_korean_day(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'mail.db')
            ident = store.add('acct', 'uid-1', mail())
            # Collected 2026-09-11 00:30 KST, which is the previous UTC day.
            store.db.execute('UPDATE mail SET received=? WHERE id=?', ('2026-09-10T15:30:00+00:00', ident))
            store.db.commit()
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': 's', 'attachments': []}, RESULT)
            store.db.execute('UPDATE mail SET analyzed_at=? WHERE id=?', ('2026-09-10T16:00:00+00:00', ident))
            store.db.commit()
            counts = store.day_counts('acct', *day_bounds(datetime.date(2026, 9, 11)))
            self.assertEqual((counts['collected'], counts['analyzed'], counts['exported']), (1, 1, 0))
            empty = store.day_counts('acct', *day_bounds(datetime.date(2026, 9, 10)))
            self.assertEqual(empty['collected'], 0)
            store.db.close()


class OverviewTests(unittest.TestCase):
    TODAY = datetime.date(2026, 9, 11)

    def rows(self, folder):
        store = Store(Path(folder) / 'mail.db')
        urgent = dict(RESULT, priority='긴급', category='견적·계약',
                      events=[{'title': '견적 회신', 'start': '', 'deadline': '2026-09-14',
                               'evidence': 'e', 'needs_review': False}])
        far = dict(RESULT, priority='낮음', category='공지', reply_needed=False,
                   reply_subject='', reply_draft='',
                   events=[{'title': '정기 점검', 'start': '', 'deadline': '2026-10-20',
                            'evidence': 'e', 'needs_review': False}])
        ids = {}
        for uid, result, stamp in (('uid-1', urgent, '2026-09-11T01:00:00+00:00'),
                                   ('uid-2', far, '2026-09-11T02:00:00+00:00')):
            ids[uid] = store.add('acct', uid, mail())
            store.analyzed(ids[uid], {'sender': 'a@b.c', 'subject': 's', 'attachments': []}, result)
            store.db.execute('UPDATE mail SET received=? WHERE id=?', (stamp, ids[uid]))
        ids['uid-3'] = store.add('acct', 'uid-3', mail())   # collected, not analysed yet
        # Stamped like the others: store.add() uses the real clock, so leaving it made
        # the 최근 7일 assertion below pass only when run on TODAY itself.
        store.db.execute('UPDATE mail SET received=? WHERE id=?',
                         ('2026-09-11T03:00:00+00:00', ids['uid-3']))
        store.db.commit()
        return store, ids

    def test_cards_count_what_the_screen_shows(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _ = self.rows(folder)
            data = overview(store.page('acct'), self.TODAY)
            self.assertEqual(data['cards']['미처리 메일'], 2)      # analysed and not handled
            self.assertEqual(data['cards']['긴급·높음'], 1)
            self.assertEqual(data['cards']['7일 내 마감'], 1)      # October deadline excluded
            self.assertEqual(data['cards']['검토 전 초안'], 1)     # the 공지 needs no reply
            self.assertEqual(data['waiting'], 1)
            store.db.close()

    def test_handled_mail_leaves_the_cards_and_the_deadlines(self):
        with tempfile.TemporaryDirectory() as folder:
            store, ids = self.rows(folder)
            store.set_handled(ids['uid-1'], HANDLED)   # the one with the 09-14 deadline
            data = overview(store.page('acct'), self.TODAY)
            self.assertEqual(data['cards']['미처리 메일'], 1)
            self.assertEqual(data['cards']['7일 내 마감'], 0)
            self.assertEqual(data['upcoming'], [])
            store.db.close()

    def test_missed_deadlines_stay_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _ = self.rows(folder)
            late = dict(RESULT, events=[{'title': '지난 회신', 'start': '', 'deadline': '2026-09-04',
                                         'evidence': 'e', 'needs_review': False}])
            ident = store.add('acct', 'uid-4', mail())
            store.analyzed(ident, {'sender': 'a@b.c', 'subject': 's', 'attachments': []}, late)
            data = overview(store.page('acct'), self.TODAY)
            self.assertEqual([entry.label for _, entry in data['past_due']], ['■ 지난 회신'])
            self.assertEqual(data['cards']['7일 내 마감'], 1)   # a missed deadline is not 임박
            store.set_handled(ident, HANDLED)
            self.assertEqual(overview(store.page('acct'), self.TODAY)['past_due'], [])
            store.db.close()

    def test_past_due_lists_the_nearest_miss_first(self):
        events = collect([['1:0', 'mail-old', '오래된 마감', '', '2026-08-01', '', '', ''],
                          ['2:0', 'mail-new', '어제 마감', '', '2026-09-10', '', '', ''],
                          ['3:0', 'mail-next', '앞으로', '', '2026-09-14', '', '', '']])
        self.assertEqual([entry.label for _, entry in past_due(events, self.TODAY)],
                         ['■ 어제 마감', '■ 오래된 마감'])
        self.assertEqual(past_due(events, self.TODAY, limit=1)[0][0], datetime.date(2026, 9, 10))

    def test_edited_draft_is_no_longer_review_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            store, ids = self.rows(folder)
            store.set_draft(ids['uid-1'], '사람이 고친 초안')   # the only one needing a reply
            self.assertEqual(overview(store.page('acct'), self.TODAY)['cards']['검토 전 초안'], 0)
            store.db.close()

    def test_distributions_and_recent_week(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _ = self.rows(folder)
            data = overview(store.page('acct'), self.TODAY)
            self.assertEqual(data['categories']['견적·계약'], 1)
            self.assertEqual(data['categories']['공지'], 1)
            self.assertEqual(data['priorities'], {'긴급': 1, '높음': 0, '보통': 0, '낮음': 1})
            self.assertEqual(len(data['recent']), 7)
            self.assertEqual(data['recent'][-1], ('2026-09-11', 3))   # includes the unanalysed one
            store.db.close()

    def test_broken_result_json_does_not_break_the_screen(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _ = self.rows(folder)
            store.db.execute("UPDATE mail SET result='{망가진' WHERE uid='uid-1'")
            store.db.commit()
            data = overview(store.page('acct'), self.TODAY)
            self.assertEqual(data['cards']['긴급·높음'], 0)
            self.assertEqual(data['categories']['견적·계약'], 0)
            store.db.close()

    def test_calendar_reuses_the_schedule_shape(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _ = self.rows(folder)
            events = overview(store.page('acct'), self.TODAY)['events']
            text, _ = month_grid(2026, 9, events)
            self.assertIn('견적 회신', ''.join(''.join(line) for line in text))
            store.db.close()


class CredentialTests(unittest.TestCase):
    def test_unicode_password_roundtrip(self):
        credentials = {}
        def write(value, flags):
            blob = value['CredentialBlob']
            if not isinstance(blob, str):
                raise TypeError('Objects of type bytes can not be converted to Unicode')
            credentials[value['TargetName']] = {'CredentialBlob': blob.encode('utf-16-le')}
        api = types.SimpleNamespace(
            CRED_TYPE_GENERIC=1, CRED_PERSIST_LOCAL_MACHINE=2,
            CredWrite=write, CredRead=lambda target, kind: credentials[target],
        )
        with patch.dict('sys.modules', {'win32cred': api}):
            for password in ('ascii-password!123', '한글 비밀번호🔐', ' leading and trailing '):
                with self.subTest(password_type='unicode'):
                    save_password('Test@Example.com', password)
                    self.assertEqual(read_password('test@example.com'), password)

    def api(self, failure):
        def read(target, kind):
            raise failure
        return types.SimpleNamespace(CRED_TYPE_GENERIC=1, CredRead=read)

    def test_missing_credential_is_a_lookup_error(self):
        failure = OSError('Element not found.')
        failure.winerror = 1168
        with patch.dict('sys.modules', {'win32cred': self.api(failure)}):
            with self.assertRaises(LookupError):
                read_password('test@example.com')

    def test_other_windows_failures_propagate(self):
        # '없음' would send the user to re-enter a password that is already stored.
        failure = OSError('The Credential Manager service is disabled.')
        failure.winerror = 1058
        with patch.dict('sys.modules', {'win32cred': self.api(failure)}):
            with self.assertRaises(OSError) as caught:
                read_password('test@example.com')
        self.assertEqual(caught.exception.winerror, 1058)


class Cell:
    def __init__(self, sheet, row, col):
        self.sheet, self.Row, self.col = sheet, row, col
    def End(self, direction):
        return Cell(self.sheet, max(r for r, c in self.sheet.data if c == 1), 1)


class Range:
    def __init__(self, sheet, first, last):
        self.sheet, self.first, self.last = sheet, first, last
        self.NumberFormat = None
    @property
    def Value(self):
        return tuple(tuple(self.sheet.data.get((r, c)) for c in range(self.first.col, self.last.col + 1)) for r in range(self.first.Row, self.last.Row + 1))
    @Value.setter
    def Value(self, values):
        if self.NumberFormat != '@':
            raise AssertionError('text format required before setting values')
        for r, row in enumerate(values, self.first.Row):
            for c, value in enumerate(row, self.first.col):
                self.sheet.data[r, c] = value


class Row:
    def __init__(self, sheet, row):
        self.sheet, self.row = sheet, row
    def Delete(self):
        self.sheet.data = {(r - 1 if r > self.row else r, c): value
                           for (r, c), value in self.sheet.data.items() if r != self.row}


class Rows:
    Count = 1048576
    def __init__(self, sheet): self.sheet = sheet
    def __call__(self, row): return Row(self.sheet, row)


class Sheet:
    def __init__(self, data=None):
        self.data = {(1, 1): 'ID'} if data is None else dict(data)
        self.Rows = Rows(self)
        self.ListObjects = MagicMock()
    def Cells(self, row, col): return Cell(self, row, col)
    def Range(self, first, last): return Range(self, first, last)


class ExcelTests(unittest.TestCase):
    def test_retry_dedup_and_preserve_user_edit(self):
        sheet = Sheet()
        append_missing(sheet, [['a', '생성 초안']], 2)
        sheet.data[2, 2] = '사용자 수정'
        append_missing(sheet, [['a', '생성 초안'], ['b', '=HYPERLINK("bad")']], 2)
        self.assertEqual(sheet.data[2, 2], '사용자 수정')
        self.assertEqual(sheet.data[3, 2], '=HYPERLINK("bad")')
        self.assertEqual(len([k for k in sheet.data if k[1] == 1]), 3)

    def adapter(self, folder, running, readonly=False):
        path = Path(folder) / 'sample.xlsx'
        path.touch()
        pythoncom = MagicMock()
        pythoncom.com_error = OSError
        client = MagicMock()
        app, book = MagicMock(), MagicMock()
        app.Ready = True
        book.ReadOnly = readonly
        book.Application = app
        app.Workbooks.Open.return_value = book
        client.DispatchEx.return_value = app
        client.Dispatch.return_value = book
        moniker = MagicMock()
        moniker.GetDisplayName.return_value = str(path)
        pythoncom.GetRunningObjectTable.return_value.EnumRunning.return_value = [moniker] if running else []
        modules = {'pythoncom': pythoncom, 'win32com': types.ModuleType('win32com'), 'win32com.client': client}
        modules['win32com'].client = client
        return path, modules, app, book

    def test_running_workbook_saved_but_never_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path, modules, app, book = self.adapter(folder, True)
            with patch.dict('sys.modules', modules), patch.object(Excel, '_write'):
                Excel(path).update([], {})
            book.Save.assert_called_once()
            book.Close.assert_not_called()
            app.Quit.assert_not_called()

    def test_owned_excel_cleanup_after_save_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path, modules, app, book = self.adapter(folder, False)
            book.Save.side_effect = OSError('locked')
            with patch.dict('sys.modules', modules), patch.object(Excel, '_write'):
                with self.assertRaisesRegex(ExcelUpdateError, '엑셀 파일 저장.*locked'):
                    Excel(path).update([], {})
            book.Close.assert_called_once_with(SaveChanges=False)
            app.Quit.assert_called_once()

    def test_readonly_workbook_is_not_written(self):
        with tempfile.TemporaryDirectory() as folder:
            path, modules, app, book = self.adapter(folder, True, True)
            with patch.dict('sys.modules', modules), patch.object(Excel, '_write') as write:
                with self.assertRaises(RuntimeError):
                    Excel(path).update([], {})
            write.assert_not_called()
            book.Save.assert_not_called()
            app.Quit.assert_not_called()

    def test_table_created_with_explicit_header_flag(self):
        sheet, head = MagicMock(), MagicMock()
        sheet.ListObjects.Count = 0
        ensure_table(sheet, head, 9)
        source = sheet.Range.return_value
        sheet.ListObjects.Add.assert_called_once_with(SourceType=1, Source=source, XlListObjectHasHeaders=1)
        sheet.ListObjects.Count = 1
        ensure_table(sheet, head, 9)
        self.assertEqual(sheet.ListObjects.Add.call_count, 1)

    def test_named_arguments_fall_back_to_positional_add(self):
        sheet, head = MagicMock(), MagicMock()
        sheet.ListObjects.Count = 0
        sheet.ListObjects.Add.side_effect = [TypeError('named arguments unsupported'), None]
        ensure_table(sheet, head, 9)
        self.assertEqual(sheet.ListObjects.Add.call_args, call(1, sheet.Range.return_value))

    def test_generated_column_headers_are_repaired(self):
        headers = HEADERS['메일 목록']
        data = {(1, c): f'Column{c}' for c in range(1, len(headers) + 1)}
        data.update({(2, c): value for c, value in enumerate(headers, 1)})
        data.update({(3, 1): 'mail-1', (3, 2): '2026-09-10T00:00:00+00:00'})
        sheet = Sheet(data)
        head = sheet.Range(sheet.Cells(1, 1), sheet.Cells(1, len(headers)))
        self.assertTrue(repair_generated_header(sheet, head, headers))
        self.assertEqual(row_text(sheet, 1, len(headers)), headers)
        self.assertEqual(sheet.data[2, 1], 'mail-1')  # data row moved up, header copy removed
        self.assertNotIn((4, 1), sheet.data)

    def test_generated_header_above_real_data_keeps_the_data(self):
        headers = HEADERS['메일 목록']
        data = {(1, c): f'Column{c}' for c in range(1, len(headers) + 1)}
        data[2, 1] = 'mail-1'
        sheet = Sheet(data)
        head = sheet.Range(sheet.Cells(1, 1), sheet.Cells(1, len(headers)))
        self.assertTrue(repair_generated_header(sheet, head, headers))
        self.assertEqual(row_text(sheet, 1, len(headers)), headers)
        self.assertEqual(sheet.data[2, 1], 'mail-1')

    def test_correct_headers_are_not_touched(self):
        headers = HEADERS['메일 목록']
        sheet = Sheet({(1, c): value for c, value in enumerate(headers, 1)})
        head = sheet.Range(sheet.Cells(1, 1), sheet.Cells(1, len(headers)))
        self.assertFalse(repair_generated_header(sheet, head, headers))
        self.assertEqual(row_text(sheet, 1, len(headers)), headers)

    def test_header_spacing_difference_is_rewritten_not_rejected(self):
        headers = HEADERS['메일 목록']
        sheets = {}
        for name, columns in HEADERS.items():
            sheet = MagicMock()
            spaced = [columns[0] + ' '] + list(columns[1:])
            sheet.Range.return_value.Value = (tuple(spaced),)
            sheet.ListObjects.Count = 0  # skip the resize pass, which needs a live sheet
            sheets[name] = sheet
        book = MagicMock()
        book.Worksheets.side_effect = sheets.__getitem__
        Excel._write(book, [], {})
        self.assertEqual(sheets['메일 목록'].Range.return_value.Value, (tuple(headers),))

    def test_renamed_column_names_every_mismatch(self):
        headers = list(HEADERS['메일 목록'])
        sheets = {}
        for name, columns in HEADERS.items():
            sheet = MagicMock()
            wrong = list(columns)
            if len(wrong) > 2:
                wrong[2] = '보낸사람'
            sheet.Range.return_value.Value = (tuple(wrong),)
            sheet.ListObjects.Count = 0
            sheets[name] = sheet
        book = MagicMock()
        book.Worksheets.side_effect = sheets.__getitem__
        with self.assertRaisesRegex(RuntimeError, 'C1: 현재 "보낸사람" / 기대 "발신자"'):
            Excel._write(book, [], {})

    def test_header_only_sheet_recovers_missing_table(self):
        from mail_assistant.core import HEADERS
        sheets = {}
        for name, headers in HEADERS.items():
            sheet = MagicMock()
            sheet.Range.return_value.Value = (tuple(headers),)
            sheet.ListObjects.Count = 0
            sheets[name] = sheet
        book = MagicMock()
        book.Worksheets.side_effect = sheets.__getitem__
        Excel._write(book, [], {})
        for name, sheet in sheets.items():
            if name != '실행 상태':
                sheet.ListObjects.Add.assert_called_once_with(
                    SourceType=1, Source=sheet.Range.return_value, XlListObjectHasHeaders=1)
            else:
                sheet.ListObjects.Add.assert_not_called()

    def test_cleanup_failure_does_not_hide_save_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path, modules, app, book = self.adapter(folder, False)
            book.Save.side_effect = OSError('save failed')
            app.Quit.side_effect = OSError('quit failed')
            with patch.dict('sys.modules', modules), patch.object(Excel, '_write'):
                with self.assertRaisesRegex(ExcelUpdateError, '엑셀 파일 저장.*save failed'):
                    Excel(path).update([], {})
            modules['pythoncom'].CoUninitialize.assert_called_once()

    def test_com_error_description_and_codes(self):
        error = RuntimeError('generic message')
        error.hresult = -2147352567
        error.excepinfo = (0, 'Microsoft Excel', 'LinkSource is invalid', None, 0, -2146827284)
        detail = error_detail(ExcelUpdateError('표 만들기', error))
        self.assertIn('표 만들기', detail)
        self.assertIn('LinkSource is invalid', detail)
        self.assertIn('0x80020009', detail)
        self.assertIn('0x800A03EC', detail)


class SettingsTests(unittest.TestCase):
    VALID = {'email': ' user@example.com ', 'host': 'pop3s.hiworks.com', 'port': '995',
             'interval': '180', 'workbook': 'C:\\Users\\a\\메일.xlsx', 'model': '', 'password': 'secret'}

    def test_values_are_trimmed_and_typed(self):
        result = normalize(self.VALID)
        self.assertEqual(result['email'], 'user@example.com')
        self.assertEqual((result['port'], result['interval']), (995, 180))
        self.assertNotIn('password', result)      # never written to config.json

    def test_rejects_bad_settings(self):
        for field, value, message in (('email', 'nope', '메일 계정'), ('host', '', '수신 서버'),
                                      ('interval', '30', '60초 이상'), ('port', '70000', '1~65535'),
                                      ('port', 'abc', '숫자'), ('workbook', '메일.xlsx', '절대 경로'),
                                      ('workbook', 'C:\\a\\메일.xls', '.xlsx')):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, message):
                    normalize({**self.VALID, field: value})


class FieldErrorTests(unittest.TestCase):
    """The inline form and the save gate must never disagree about what is valid."""

    VALID = SettingsTests.VALID

    def test_valid_settings_have_no_field_errors(self):
        self.assertEqual(field_errors(self.VALID), {})

    def test_each_bad_field_is_named(self):
        for field, value in (('email', 'nope'), ('host', ''), ('interval', '30'),
                             ('port', '70000'), ('port', 'abc'), ('workbook', '메일.xlsx'),
                             ('workbook', 'C:\\a\\메일.xls')):
            with self.subTest(field=field, value=value):
                errors = field_errors({**self.VALID, field: value})
                self.assertIn(field, errors)

    def test_the_two_checks_agree_on_every_case(self):
        cases = [self.VALID,
                 {**self.VALID, 'email': 'nope'},
                 {**self.VALID, 'host': ''},
                 {**self.VALID, 'port': 'abc'},
                 {**self.VALID, 'port': '0'},
                 {**self.VALID, 'interval': '59'},
                 {**self.VALID, 'interval': ''},
                 {**self.VALID, 'workbook': ''},
                 {**self.VALID, 'workbook': 'C:\\a\\b.xls'},
                 {**self.VALID, 'model': 'gpt-5.6-luna'},
                 {**self.VALID, 'model': '두 단어'},
                 {}]
        for values in cases:
            with self.subTest(values=values):
                try:
                    normalize(values)
                except ValueError:
                    refused = True
                else:
                    refused = False
                self.assertEqual(refused, bool(field_errors(values)))


class ModelChoiceTests(unittest.TestCase):
    """The radio is built from Codex's own cache, so a retired slug cannot linger."""

    CACHE = {'models': [
        {'slug': 'gpt-6-astra', 'display_name': 'GPT-6-Astra', 'visibility': 'list',
         'description': '가장 뛰어난 모델'},
        {'slug': 'gpt-reserve', 'display_name': 'GPT-Reserve', 'visibility': 'hide',
         'description': '숨김'},
        {'slug': 'gpt-5.6-luna', 'display_name': 'GPT-5.6-Luna', 'visibility': 'list'},
    ]}

    def test_only_the_models_the_account_may_pick_are_offered(self):
        self.assertEqual([slug for slug, _, _ in model_choices(self.CACHE)],
                         ['gpt-6-astra', 'gpt-5.6-luna'])

    def test_the_hint_leads_with_the_slug_that_goes_on_the_command_line(self):
        self.assertEqual(model_choices(self.CACHE)[0][2], 'gpt-6-astra · 가장 뛰어난 모델')

    def test_a_model_with_no_description_is_still_named_by_its_slug(self):
        self.assertEqual(model_choices(self.CACHE)[1][1:], ('GPT-5.6-Luna', 'gpt-5.6-luna'))

    def test_a_cache_of_another_shape_is_no_models_rather_than_a_crash(self):
        for data in (None, {}, {'models': None}, {'models': ['gpt-6-astra']}, 'text'):
            with self.subTest(data=data):
                self.assertEqual(model_choices(data), [])

    def test_the_rows_open_with_the_default_and_close_with_the_custom_box(self):
        rows = model_rows(model_choices(self.CACHE))
        self.assertEqual(rows[0][0], '')
        self.assertEqual(rows[-1][0], CUSTOM)

    def test_a_saved_model_codex_no_longer_lists_keeps_its_own_row(self):
        rows = model_rows(model_choices(self.CACHE), 'gpt-5.1-codex')
        self.assertIn('gpt-5.1-codex', [value for value, _, _ in rows])

    def test_a_saved_model_codex_still_lists_is_not_repeated(self):
        rows = model_rows(model_choices(self.CACHE), 'gpt-6-astra')
        self.assertEqual([value for value, _, _ in rows].count('gpt-6-astra'), 1)

    def test_the_custom_row_saves_the_box_and_the_others_save_themselves(self):
        self.assertEqual(model_value(CUSTOM, ' gpt-9 '), 'gpt-9')
        self.assertEqual(model_value('gpt-6-astra', 'ignored'), 'gpt-6-astra')
        self.assertEqual(model_value('', ''), '')

    def test_a_missing_cache_is_an_empty_list_and_never_an_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(read_models(Path(folder) / 'models_cache.json'), [])
            broken = Path(folder) / 'broken.json'
            broken.write_text('{not json', encoding='utf-8')
            self.assertEqual(read_models(broken), [])

    def test_a_real_cache_is_read_from_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'models_cache.json'
            path.write_text(json.dumps(self.CACHE, ensure_ascii=False), encoding='utf-8')
            self.assertEqual([slug for slug, _, _ in read_models(path)],
                             ['gpt-6-astra', 'gpt-5.6-luna'])


class TestClient:
    def __init__(self, count=2, fail_pass=False):
        self.count, self.fail_pass, self.quit_called = count, fail_pass, False
    def user(self, user): pass
    def pass_(self, password):
        if self.fail_pass:
            raise poplib.error_proto(b'-ERR invalid password')
    def uidl(self):
        return b'+OK', [f'{index} uid-{index}'.encode() for index in range(1, self.count + 1)], 0
    def quit(self): self.quit_called = True
    def close(self): pass


class ConnectionCheckTests(unittest.TestCase):
    CONFIG = {'host': 'pop3s.hiworks.com', 'port': 995, 'email': 'user@example.com'}

    def test_reports_mailbox_size_and_always_quits(self):
        client = TestClient(count=2)
        message = check_connection(self.CONFIG, 'secret', factory=lambda *a, **k: client)
        self.assertIn('2건', message)
        self.assertTrue(client.quit_called)

    def test_authentication_failure_propagates(self):
        client = TestClient(fail_pass=True)
        with self.assertRaises(poplib.error_proto):
            check_connection(self.CONFIG, 'wrong', factory=lambda *a, **k: client)
        self.assertTrue(client.quit_called)

    def test_login_state_maps_the_three_outcomes(self):
        with patch('mail_assistant.services.check_login'):
            self.assertEqual(login_state()[0], '연결됨')
        with patch('mail_assistant.services.check_login', side_effect=RuntimeError('codex login 실행')):
            state, detail = login_state()
            self.assertEqual(state, '로그인 필요')
            self.assertIn('codex login', detail)
        with patch('mail_assistant.services.check_login', side_effect=OSError('없음')):
            self.assertEqual(login_state()[0], '확인 실패')


class ConnectionStepTests(unittest.TestCase):
    """The 진단 page's step list. It stops at the first failure and never echoes the password."""

    CONFIG = {'host': 'pop3s.hiworks.com', 'port': 995, 'email': 'me@corp.example'}

    def test_every_step_passes_on_a_healthy_mailbox(self):
        client = TestClient()
        steps = connection_steps(self.CONFIG, 'secret', factory=lambda *a, **k: client,
                                 resolve=lambda host: '203.0.113.7')
        self.assertTrue(all(ok for _, ok, _ in steps))
        self.assertEqual([label for label, _, _ in steps],
                         ['주소 조회', 'SSL 연결 995', '계정 전송', '비밀번호 인증', '사서함 조회'])
        self.assertIn('203.0.113.7', steps[0][2])

    def test_a_dns_failure_stops_before_connecting(self):
        def refuse(host):
            raise OSError('이름을 확인할 수 없습니다')
        steps = connection_steps(self.CONFIG, 'secret', factory=lambda *a, **k: 1 / 0,
                                 resolve=refuse)
        self.assertEqual(len(steps), 1)
        self.assertFalse(steps[0][1])

    def test_a_connect_failure_stops_before_authenticating(self):
        def refuse(*args, **kwargs):
            raise OSError('연결이 거부되었습니다')
        steps = connection_steps(self.CONFIG, 'secret', factory=refuse,
                                 resolve=lambda host: '203.0.113.7')
        self.assertEqual([label for label, _, _ in steps], ['주소 조회', 'SSL 연결 995'])
        self.assertFalse(steps[-1][1])

    def test_a_wrong_password_is_reported_without_printing_it(self):
        class Refuses(TestClient):
            def pass_(self, password):
                raise poplib.error_proto(f'-ERR invalid password {password}')
        steps = connection_steps(self.CONFIG, 'secret', factory=lambda *a, **k: Refuses(),
                                 resolve=lambda host: '203.0.113.7')
        self.assertFalse(steps[-1][1])
        detail = steps[-1][2]
        self.assertNotIn('secret', detail)
        self.assertIn('***', detail)


class ChatReplyTests(unittest.TestCase):
    """A chat turn reuses analyze()'s hardened invocation, schema and all."""

    def run_chat(self, written=None, returncode=0):
        seen = {}

        def fake_run(command, **kwargs):
            seen['command'] = command
            seen['input'] = kwargs.get('input', '')
            target = command[command.index('-o') + 1]
            if written is not None:
                Path(target).write_text(json.dumps(written, ensure_ascii=False), encoding='utf-8')
            return types.SimpleNamespace(returncode=returncode, stdout='', stderr='')

        with patch('mail_assistant.services.codex_command', return_value=['codex']), \
             patch('mail_assistant.services.subprocess.run', side_effect=fake_run):
            try:
                reply = chat_reply('언제까지 회신해야 하나요?',
                                   history=[('user', '이전 질문'), ('codex', '이전 답')],
                                   mail={'subject': '견적 요청', 'body': '9월 14일까지'},
                                   config={'model': 'gpt-5'})
            except Exception as exc:
                return seen, exc
        return seen, reply

    def test_the_answer_comes_back_from_the_schema_file(self):
        seen, reply = self.run_chat({'reply': '9월 14일까지입니다.'})
        self.assertEqual(reply, '9월 14일까지입니다.')

    def test_the_sandbox_flags_are_the_same_as_analysis(self):
        seen, _ = self.run_chat({'reply': 'ok'})
        command = seen['command']
        self.assertIn('--sandbox', command)
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn('--ephemeral', command)
        self.assertEqual(command[command.index('--model') + 1], 'gpt-5')

    def test_the_prompt_carries_the_injection_guard_and_the_transcript(self):
        seen, _ = self.run_chat({'reply': 'ok'})
        self.assertIn('신뢰하지 않는 입력', seen['input'])
        self.assertIn('도구를 사용하지 마세요', seen['input'])
        self.assertIn('이전 질문', seen['input'])
        self.assertIn('견적 요청', seen['input'])

    def test_a_failed_turn_says_nothing_about_stdout(self):
        seen, error = self.run_chat(None, returncode=1)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn('Codex 응답을 받지 못했습니다', str(error))

    def test_the_schema_allows_only_a_reply_string(self):
        shape = chat_schema()
        self.assertEqual(shape['required'], ['reply'])
        self.assertFalse(shape['additionalProperties'])


class MailtoTests(unittest.TestCase):
    def test_address_is_taken_from_the_display_form(self):
        self.assertEqual(address_of('김철수 <kim@a.com>'), 'kim@a.com')
        self.assertEqual(address_of('kim@a.com'), 'kim@a.com')
        self.assertEqual(address_of('이름만 있음'), '')

    def test_subject_and_body_are_encoded(self):
        link = mailto('김철수 <kim@a.com>', 'Re: 견적', '안녕하세요.\n회신드립니다.')
        self.assertTrue(link.startswith('mailto:kim@a.com?subject='))
        self.assertIn('&body=', link)
        self.assertNotIn(' ', link)
        self.assertNotIn('\n', link)

    def test_long_drafts_stay_under_the_hyperlink_limit(self):
        self.assertLessEqual(len(mailto('a <a@b.c>', 'x' * 300, 'y' * 9000)), 1800)

    def test_no_address_means_no_link(self):
        sheet = MagicMock()
        self.assertEqual(mailto('이름만', '제목', '초안'), '')
        link_to_draft(sheet, '답변 초안', 5, ['m1', '이름만', '제목', '초안', '', '검토 전'])
        sheet.Hyperlinks.Add.assert_not_called()
        link_to_draft(sheet, '답변 초안', 5, ['m1', 'a <a@b.c>', '제목', '초안', '', '검토 전'])
        arguments = sheet.Hyperlinks.Add.call_args.kwargs
        self.assertTrue(arguments['Address'].startswith('mailto:a@b.c'))
        sheet.Cells.assert_called_with(5, 3)     # 답변 제목 column


ROW_VALUES = ['m1', '김철수 <kim@example.com>', 'Re: 견적', '확인 후 회신드리겠습니다.', '', '검토 전']


class FirstColumnTests(unittest.TestCase):
    """COM's Range.Value has three shapes and only one of them is a tuple of rows."""

    def test_a_multi_cell_range_is_a_tuple_of_rows(self):
        self.assertEqual(first_column((('a',), ('b',), (None,))), ['a', 'b', None])

    def test_a_single_cell_range_is_a_bare_scalar(self):
        self.assertEqual(first_column('a'), ['a'])
        self.assertEqual(first_column(7), [7])

    def test_a_single_empty_cell_is_none(self):
        self.assertEqual(first_column(None), [])

    def test_a_one_cell_string_is_not_read_character_by_character(self):
        """The bug this replaced compared mail ids against the letters of one id."""
        self.assertEqual(first_column('abc123'), ['abc123'])

    def test_a_flat_tuple_is_accepted_too(self):
        self.assertEqual(first_column(('a', 'b')), ['a', 'b'])


class LinkTests(unittest.TestCase):
    def test_mail_rows_maps_ids_to_row_numbers(self):
        sheet = Sheet({(1, 1): '메일 ID', (2, 1): 'm1', (3, 1): 'm2', (4, 1): 'm3'})
        self.assertEqual(mail_rows(sheet), {'m1': 2, 'm2': 3, 'm3': 4})
        self.assertEqual(mail_rows(Sheet({(1, 1): '메일 ID'})), {})

    def test_link_points_at_the_mail_id_column_of_each_sheet(self):
        sheet = MagicMock()
        link_to_mail(sheet, '일정', 7, 5)
        arguments = sheet.Hyperlinks.Add.call_args.kwargs
        self.assertEqual(arguments['SubAddress'], "'메일 목록'!A5")
        self.assertEqual(arguments['Address'], '')
        sheet.Cells.assert_called_with(7, 2)      # 일정 keeps 메일 ID in column B
        link_to_mail(sheet, '우선순위', 7, 5)
        sheet.Cells.assert_called_with(7, 1)      # 우선순위 keeps it in column A

    def test_write_links_new_rows_but_not_the_mail_list(self):
        sheets = {}
        for name, columns in HEADERS.items():
            sheet = MagicMock()
            sheet.Range.return_value.Value = (tuple(columns),)
            sheet.ListObjects.Count = 0
            sheets[name] = sheet
        book = MagicMock()
        book.Worksheets.side_effect = sheets.__getitem__
        row = {'id': 'm1', 'received': '2026-09-11T00:00:00+00:00',
               'parsed': json.dumps({'sender': 'a@b.c', 'subject': 's', 'attachments': []}),
               'result': json.dumps(RESULT)}
        with patch('mail_assistant.excel.append_missing', return_value=[(7, ROW_VALUES)]), \
             patch('mail_assistant.excel.mail_rows', return_value={'m1': 5}), \
             patch('mail_assistant.excel.link_to_mail') as linked, \
             patch('mail_assistant.excel.sheet_events', return_value={}), \
             patch('mail_assistant.excel.apply_style'), \
             patch('mail_assistant.excel.update_calendar'), \
             patch('mail_assistant.excel.update_dashboard'):
            Excel._write(book, [row], {})
        self.assertEqual({call.args[1] for call in linked.call_args_list},
                         {'일정', '우선순위', '답변 초안'})
        self.assertTrue(all(call.args[2:] == (7, 5) for call in linked.call_args_list))

    def test_unknown_mail_id_is_left_unlinked(self):
        sheets = {}
        for name, columns in HEADERS.items():
            sheet = MagicMock()
            sheet.Range.return_value.Value = (tuple(columns),)
            sheet.ListObjects.Count = 0
            sheets[name] = sheet
        book = MagicMock()
        book.Worksheets.side_effect = sheets.__getitem__
        row = {'id': 'm1', 'received': '2026-09-11T00:00:00+00:00',
               'parsed': json.dumps({'sender': 'a@b.c', 'subject': 's', 'attachments': []}),
               'result': json.dumps(RESULT)}
        with patch('mail_assistant.excel.append_missing', return_value=[(7, ROW_VALUES)]), \
             patch('mail_assistant.excel.mail_rows', return_value={}), \
             patch('mail_assistant.excel.link_to_mail') as linked, \
             patch('mail_assistant.excel.sheet_events', return_value={}), \
             patch('mail_assistant.excel.apply_style'), \
             patch('mail_assistant.excel.update_calendar'), \
             patch('mail_assistant.excel.update_dashboard'):
            Excel._write(book, [row], {})
        linked.assert_not_called()


class CalendarTests(unittest.TestCase):
    TODAY = datetime.date(2026, 9, 10)
    ROWS = [
        ['m1:0', 'm1', '견적 회신 마감', '', '2026-09-16T18:00:00+09:00', '9월 16일까지', '', '미처리'],
        ['m1:1', 'm1', '사양 확정 회의', '2026-09-14T10:00:00+09:00', '', '', '', '미처리'],
        ['m2:0', 'm2', '유지보수 미팅', '2026-09-15', '', '', '필요', '미처리'],
        ['m3:0', 'm3', '도면 회신', '', '2026-08-25', '', '', '미처리'],
    ]

    def test_parse_day_accepts_iso_only(self):
        self.assertEqual(parse_day('2026-09-16'), (datetime.date(2026, 9, 16), ''))
        self.assertEqual(parse_day('2026-09-16T18:00:00+09:00'), (datetime.date(2026, 9, 16), '18:00'))
        self.assertEqual(parse_day(''), (None, ''))
        self.assertEqual(parse_day('다음 주 화요일'), (None, ''))
        self.assertEqual(parse_day('2026-02-30'), (None, ''))

    def test_collect_splits_start_deadline_and_review(self):
        events = collect(self.ROWS)
        deadline = events[datetime.date(2026, 9, 16)][0]
        self.assertEqual((deadline.label, deadline.kind), ('■ 18:00 견적 회신 마감', '마감'))
        self.assertEqual(deadline.mail_id, 'm1')
        self.assertEqual(deadline.row, 2)          # first data row of the 일정 sheet
        start = events[datetime.date(2026, 9, 14)][0]
        self.assertEqual((start.label, start.kind, start.row), ('▶ 10:00 사양 확정 회의', '시작', 3))
        # 확인 필요 wins over the start/deadline colour.
        review = events[datetime.date(2026, 9, 15)][0]
        self.assertEqual((review.label, review.kind), ('◆ 유지보수 미팅', '확인 필요'))

    def test_cell_text_colour_spans_line_up(self):
        entries = [Entry('■ 마감', '마감', 'm1', 2), Entry('▶ 시작', '시작', 'm1', 3)]
        text, spans = cell_text(datetime.date(2026, 9, 16), entries)
        self.assertEqual(text.split('\n'), ['16', '■ 마감', '▶ 시작'])
        for (start, length, color), entry in zip(spans, entries):
            self.assertEqual(text[start - 1:start - 1 + length], entry.label)
        self.assertEqual(spans[0][2], URGENT)

    def test_cell_text_caps_long_days(self):
        entries = [Entry(f'■ 일정 {index}', '마감', 'm1', 2 + index) for index in range(5)]
        text, spans = cell_text(datetime.date(2026, 9, 16), entries)
        self.assertEqual(len(spans), 3)
        self.assertTrue(text.endswith('…외 2건'))

    def test_month_grid_places_weeks_monday_first(self):
        text, cells = month_grid(2026, 9, collect(self.ROWS))
        self.assertEqual(text[0][0], '')          # 2026-09-01 is a Tuesday
        self.assertEqual(text[0][1], '1')
        self.assertEqual(text[2][2].split('\n')[0], '16')
        self.assertNotIn('도면 회신', ''.join(''.join(row) for row in text))  # August stays out
        self.assertEqual(cells[2, 0][0], datetime.date(2026, 9, 14))

    def test_overdue_lists_only_past_deadlines(self):
        self.assertEqual(overdue(collect(self.ROWS), self.TODAY),
                         [(datetime.date(2026, 8, 25), '■ 도면 회신')])

    def test_next_month_crosses_the_year(self):
        self.assertEqual(next_month(datetime.date(2026, 12, 31)), datetime.date(2027, 1, 1))
        self.assertEqual(next_month(self.TODAY), datetime.date(2026, 10, 1))

    def book(self):
        schedule = Sheet({(row, col + 1): value for row, values in enumerate(self.ROWS, 2)
                          for col, value in enumerate(values)})
        sheets = {'일정': schedule, SHEET: MagicMock()}
        book = MagicMock()
        book.Worksheets.side_effect = sheets.__getitem__
        return book, sheets

    def test_sheet_events_reads_the_schedule_rows(self):
        book, sheets = self.book()
        self.assertEqual(sheet_events(sheets['일정'], 8), collect(self.ROWS))

    def test_calendar_is_redrawn_once_per_change(self):
        book, sheets = self.book()
        events = collect(self.ROWS)
        update_calendar(book, events, self.TODAY)
        sheets[SHEET].Cells.Clear.assert_called_once()
        # A matching signature means the grid is already current.
        book.CustomDocumentProperties.return_value.Value = signature(events, self.TODAY)
        update_calendar(book, events, self.TODAY)
        sheets[SHEET].Cells.Clear.assert_called_once()

    def test_calendar_skipped_before_any_analysis(self):
        book = MagicMock()
        book.Worksheets.side_effect = KeyError('일정')
        update_calendar(book, {}, self.TODAY)   # must not raise
        book.Worksheets.Add.assert_not_called()


class StyleTests(unittest.TestCase):
    def sheets(self):
        result = {}
        for name in HEADERS:
            sheet = MagicMock()
            sheet.ListObjects.Count = 1
            result[name] = sheet
        return result

    def test_rules_use_named_arguments_and_run_once(self):
        book, sheets = MagicMock(), self.sheets()
        apply_style(book, sheets)
        conditions = sheets['우선순위'].Range.return_value.FormatConditions
        conditions.Delete.assert_called_once()
        self.assertEqual(conditions.Add.call_args_list[0], call(Type=2, Formula1='=$B2="긴급"'))
        self.assertEqual(conditions.Add.call_count, 3)
        self.assertEqual(sheets['메일 목록'].ListObjects(1).TableStyle, 'TableStyleLight8')
        # The version marker keeps the same workbook from being restyled every export.
        book.CustomDocumentProperties.return_value.Value = STYLE_VERSION
        apply_style(book, sheets)
        self.assertEqual(conditions.Add.call_count, 3)

    def test_deadline_rules_compare_iso_text(self):
        book, sheets = MagicMock(), self.sheets()
        apply_style(book, sheets)
        formulas = [call.kwargs['Formula1']
                    for call in sheets['일정'].Range.return_value.FormatConditions.Add.call_args_list]
        self.assertEqual(len(formulas), 2)
        self.assertTrue(all('LEFT($E2,10)' in formula for formula in formulas))
        self.assertIn('<TEXT(TODAY(),"yyyy-mm-dd")', formulas[0])

    def test_status_sheet_gets_no_table_or_freeze(self):
        book, sheets = MagicMock(), self.sheets()
        apply_style(book, sheets)
        status = sheets['실행 상태']
        self.assertNotIsInstance(status.ListObjects(1).TableStyle, str)
        status.Range.assert_not_called()     # no conditional formats
        status.Activate.assert_not_called()  # no freeze panes, so the view stays put
        self.assertEqual(status.Columns(1).Font.Bold, True)

    def test_cosmetic_failure_is_reported_not_raised(self):
        status = Sheet({(1, 1): '항목', (1, 2): '값'})
        sheets = {}
        for name, columns in HEADERS.items():
            if name == '실행 상태':
                sheets[name] = status
                continue
            sheet = MagicMock()
            sheet.Range.return_value.Value = (tuple(columns),)
            sheet.ListObjects.Count = 0
            sheets[name] = sheet
        book = MagicMock()
        book.Worksheets.side_effect = sheets.__getitem__
        with patch('mail_assistant.excel.update_calendar', side_effect=OSError('달력 실패')), \
             patch('mail_assistant.excel.update_dashboard', side_effect=OSError('대시보드 실패')), \
             patch('mail_assistant.excel.sheet_events', return_value={}), \
             patch('mail_assistant.excel.mail_rows', return_value={}), \
             patch('mail_assistant.excel.apply_style', side_effect=OSError('서식 실패')):
            Excel._write(book, [], {'안내': '새 메일 1건 수집'})
        written = {status.data[row, 1]: status.data[row, 2]
                   for row in range(2, status.data and max(r for r, _ in status.data) + 1)}
        self.assertEqual(written['안내'], '새 메일 1건 수집')
        self.assertIn('달력 실패', written['일정 달력 갱신'])
        self.assertIn('대시보드 실패', written['대시보드 갱신'])
        self.assertIn('서식 실패', written['서식 적용'])


class DashboardTests(unittest.TestCase):
    TODAY = datetime.date(2026, 9, 11)

    def formulas(self):
        return ' '.join(str(value) for *_, kind, value, _ in blocks(self.TODAY)
                        if str(value).startswith('='))

    def test_formulas_point_at_the_right_columns(self):
        joined = self.formulas()
        self.assertIn("""COUNTIF('메일 목록'!I:I,"미처리")""", joined)    # 처리 상태 is column I
        self.assertIn("""COUNTIF('우선순위'!B:B,"긴급")""", joined)
        self.assertIn("""COUNTIF('답변 초안'!F:F,"검토 전")""", joined)
        self.assertIn("'일정'!$E$2:$E$20000", joined)                   # 마감 is column E

    def test_layout_cells_never_collide(self):
        # The deadline list is filled in later, so reserve its cells up front.
        taken = {(row, column) for row in range(UPCOMING_TOP, UPCOMING_TOP + UPCOMING_ROWS)
                 for column in range(8, 14)}
        for row, first, width, *_ in blocks(self.TODAY):
            for column in range(first, first + width):
                self.assertNotIn((row, column), taken, f'{row},{column} 중복')
                taken.add((row, column))

    def test_upcoming_lists_nearest_deadlines_only(self):
        events = {datetime.date(2026, 9, 9): [Entry('■ 지난 건', '마감', 'm1', 2)],
                  datetime.date(2026, 9, 11): [Entry('■ 오늘 마감', '마감', 'm2', 3)],
                  datetime.date(2026, 9, 14): [Entry('▶ 착수', '시작', 'm3', 4),
                                               Entry('■ 보고서', '마감', 'm3', 5)]}
        self.assertEqual([(day, entry.label) for day, entry in upcoming(events, self.TODAY)],
                         [(datetime.date(2026, 9, 11), '■ 오늘 마감'),
                          (datetime.date(2026, 9, 14), '■ 보고서')])
        self.assertEqual(describe(datetime.date(2026, 9, 9), self.TODAY), '2일 지남')
        self.assertEqual(describe(datetime.date(2026, 9, 11), self.TODAY), '오늘')

    def test_drawn_once_but_deadlines_refresh_every_export(self):
        sheet, book = MagicMock(), MagicMock()
        cells = {}
        # One mock per cell, or every write lands on the same object.
        sheet.Cells.side_effect = lambda row, col: cells.setdefault((row, col), MagicMock())
        book.Worksheets.side_effect = {DASHBOARD: sheet}.__getitem__
        events = {datetime.date(2026, 9, 14): [Entry('■ 보고서', '마감', 'm3', 5)]}
        update_dashboard(book, events, self.TODAY, mail_rows={'m3': 7})
        sheet.Cells.Clear.assert_called_once()
        book.CustomDocumentProperties.return_value.Value = '1'
        update_dashboard(book, events, self.TODAY, mail_rows={'m3': 7})
        sheet.Cells.Clear.assert_called_once()                     # layout stays put
        self.assertEqual(cells[10, 8].Value, '2026-09-14')         # list still refilled
        self.assertEqual(cells[10, 9].Value, '■ 보고서')
        self.assertEqual(cells[10, 12].Value, '3일 뒤')
        self.assertEqual(cells[11, 9].Value, '')                   # empty rows cleared
        target = sheet.Hyperlinks.Add.call_args.kwargs
        self.assertEqual(target['SubAddress'], "'메일 목록'!A7")     # click jumps to the mail
        self.assertEqual(target['Address'], '')


class SampleFileTests(unittest.TestCase):
    def test_sample_workbook_has_every_sheet_and_the_calendar(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest('openpyxl이 없습니다')
        import sample
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.xlsx'
            with patch.object(sys, 'argv', ['sample.py', str(path)]):
                sample.main()
            book = openpyxl.load_workbook(path)
            self.assertEqual(set(HEADERS) | {'일정 달력', '대시보드'}, set(book.sheetnames))
            self.assertEqual(book.sheetnames[0], '대시보드')
            dashboard = book['대시보드']
            self.assertFalse(dashboard.sheet_view.showGridLines)
            self.assertEqual(dashboard['B2'].value, '메일 업무 도우미')
            self.assertTrue(str(dashboard['B6'].value).startswith('=COUNTIF'))
            self.assertIn('견적 회신 마감', str(dashboard['I11'].value))  # nearest deadlines listed
            for name, headers in HEADERS.items():
                self.assertEqual([cell.value for cell in book[name][1]][:len(headers)], list(headers))
            self.assertEqual(book['메일 목록'].max_row, 6)   # header + 5 sample mails
            calendar_sheet = book['일정 달력']
            self.assertEqual(calendar_sheet['A1'].value, f'{sample.TODAY.year}년 {sample.TODAY.month}월')
            grid = '\n'.join(str(cell.value) for row in calendar_sheet.iter_rows() for cell in row if cell.value)
            self.assertIn('견적 회신 마감', grid)
            self.assertIn('지난 마감', grid)


    def test_no_whitespace_run_without_preserve(self):
        """Excel repairs the file when a text run holds only whitespace."""
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest('openpyxl이 없습니다')
        import re
        import zipfile
        import sample
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.xlsx'
            with patch.object(sys, 'argv', ['sample.py', str(path)]):
                sample.main()
            with zipfile.ZipFile(path) as archive:
                parts = [name for name in archive.namelist() if name.startswith('xl/worksheets/sheet')]
                self.assertTrue(parts)
                for name in parts:
                    body = archive.read(name).decode('utf-8')
                    for tag, text in re.findall(r'<t( [^>]*)?>(.*?)</t>', body, re.S):
                        if text and text != text.strip():
                            self.assertIn('xml:space="preserve"', tag, f'{name}: {text!r}')


class WorkerTests(unittest.TestCase):
    def test_export_failure_keeps_result_and_reports_detail(self):
        from mail_assistant.worker import run
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            store = Store(directory / 'mail.db')
            ident = store.add(account_key(CONFIG), 'new', mail())
            store.analyzed(ident, parse_mail(mail()), RESULT)
            store.db.close()
            stop = MagicMock()
            # One pass through the loop. The briefing check at the end of the cycle
            # asks as well, which is why this is three and not two.
            stop.is_set.side_effect = [False, False, True]
            messages = []
            with patch('mail_assistant.worker.fetch_mail', return_value='새 메일 0건 수집'), patch('mail_assistant.worker.read_password', return_value='test'), patch('mail_assistant.worker.write_briefing'), patch('mail_assistant.worker.Excel') as excel:
                excel.return_value.update.side_effect = ExcelUpdateError('표 만들기', RuntimeError('invalid argument'))
                run({**CONFIG, 'workbook': str(directory / 'test.xlsx'), 'interval': 180}, directory, stop, messages.append)
            self.assertIn('표 만들기', messages[-1])
            self.assertIn('invalid argument', messages[-1])
            snapshot = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
            self.assertIn('invalid argument', snapshot['message'])
            store = Store(directory / 'mail.db')
            self.assertEqual(len(store.unexported(account_key(CONFIG))), 1)
            store.db.close()


class AnalyzingMarkerTests(unittest.TestCase):
    """The worker's own end of '분석 중': set before the call, gone after every exit."""

    def run_once(self, directory, analyze):
        from mail_assistant.worker import run
        stop = MagicMock()
        # One whole cycle: counting is_set() calls breaks the moment the loop grows a
        # branch, so the end of the cycle — the interval wait — is what stops it.
        stop.is_set.return_value = False
        stop.wait.side_effect = lambda *_: stop.is_set.configure_mock(return_value=True)
        messages = []
        with patch('mail_assistant.worker.fetch_mail', return_value='새 메일 0건 수집'), \
                patch('mail_assistant.worker.read_password', return_value='test'), \
                patch('mail_assistant.worker.check_login'), \
                patch('mail_assistant.worker.analyze', side_effect=analyze), \
                patch('mail_assistant.worker.Excel'):
            run({**CONFIG, 'workbook': str(directory / 'test.xlsx'), 'interval': 180},
                directory, stop, messages.append)
        return messages

    def prepared(self, folder):
        directory = Path(folder)
        store = Store(directory / 'mail.db')
        ident = store.add(account_key(CONFIG), 'new', mail())
        store.db.close()
        return directory, ident

    def test_the_mail_being_analysed_carries_the_marker_while_it_is(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, ident = self.prepared(folder)
            seen = []

            def analyze(row, config):
                store = Store(directory / 'mail.db')
                seen.append(store.analyzing(account_key(CONFIG)))
                store.db.close()
                return parse_mail(mail()), RESULT

            self.run_once(directory, analyze)
            self.assertEqual(seen, [ident])
            store = Store(directory / 'mail.db')
            self.assertEqual(store.analyzing(account_key(CONFIG)), '')
            store.db.close()

    def test_an_analysis_that_fails_still_gives_the_marker_back(self):
        """Otherwise the mail reads '분석 중' for ever and 다시 분석 refuses to touch it."""
        with tempfile.TemporaryDirectory() as folder:
            directory, ident = self.prepared(folder)
            self.run_once(directory, RuntimeError('한도 초과'))
            store = Store(directory / 'mail.db')
            self.assertEqual(store.analyzing(account_key(CONFIG)), '')
            self.assertEqual(state_of(store.detail(ident)), '1회 실패')
            store.db.close()

    def test_a_marker_left_by_a_crash_is_cleared_at_the_next_start(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, ident = self.prepared(folder)
            store = Store(directory / 'mail.db')
            store.mark_analyzing(account_key(CONFIG), ident)
            store.db.close()
            self.run_once(directory, lambda row, config: (parse_mail(mail()), RESULT))
            store = Store(directory / 'mail.db')
            self.assertEqual(store.analyzing(account_key(CONFIG)), '')
            store.db.close()


class ConsoleTests(unittest.TestCase):
    """Korean print() into a redirected pipe is the ANSI codepage on Windows."""

    def test_reconfigures_a_narrow_stream(self):
        import io
        from mail_assistant.console import use_utf8
        narrow = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        with patch.object(sys, 'stdout', narrow), patch.object(sys, 'stderr', narrow):
            use_utf8()
            print('예시 파일 생성', file=sys.stdout)
        self.assertEqual(narrow.encoding, 'utf-8')

    def test_is_safe_when_a_stream_cannot_be_reconfigured(self):
        from mail_assistant.console import use_utf8
        with patch.object(sys, 'stdout', object()), patch.object(sys, 'stderr', None):
            use_utf8()


if __name__ == '__main__':
    unittest.main()
