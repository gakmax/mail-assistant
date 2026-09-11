import datetime
import json
import poplib
import subprocess
import sys
import tempfile
import unittest
import types
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import call, patch, MagicMock

from mail_assistant.core import HEADERS, Store, account_key, parse_mail, workbook_rows
from mail_assistant.excel import (Excel, ExcelUpdateError, append_missing, ensure_table,
                                  error_detail, repair_generated_header, row_text)
from mail_assistant.calendar_sheet import (SHEET, Entry, cell_text, collect, month_grid,
                                           next_month, overdue, parse_day, sheet_events,
                                           signature, update_calendar)
from mail_assistant.dashboard import (SHEET as DASHBOARD, UPCOMING_ROWS, UPCOMING_TOP,
                                      blocks, describe, update_dashboard, upcoming)
from mail_assistant.excel import address_of, link_to_draft, link_to_mail, mail_rows, mailto
from mail_assistant.settings import normalize
from mail_assistant.services import check_connection, login_state
from mail_assistant.style import STYLE_VERSION, URGENT, apply_style
from mail_assistant.services import analyze, fetch_mail, read_password, save_password


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
        self.assertEqual((deadline.label, deadline.kind), ('◾ 18:00 견적 회신 마감', '마감'))
        self.assertEqual(deadline.mail_id, 'm1')
        self.assertEqual(deadline.row, 2)          # first data row of the 일정 sheet
        start = events[datetime.date(2026, 9, 14)][0]
        self.assertEqual((start.label, start.kind, start.row), ('▫ 10:00 사양 확정 회의', '시작', 3))
        # 확인 필요 wins over the start/deadline colour.
        review = events[datetime.date(2026, 9, 15)][0]
        self.assertEqual((review.label, review.kind), ('· 유지보수 미팅', '확인 필요'))

    def test_cell_text_colour_spans_line_up(self):
        entries = [Entry('◾ 마감', '마감', 'm1', 2), Entry('▫ 시작', '시작', 'm1', 3)]
        text, spans = cell_text(datetime.date(2026, 9, 16), entries)
        self.assertEqual(text.split('\n'), ['16', '◾ 마감', '▫ 시작'])
        for (start, length, color), entry in zip(spans, entries):
            self.assertEqual(text[start - 1:start - 1 + length], entry.label)
        self.assertEqual(spans[0][2], URGENT)

    def test_cell_text_caps_long_days(self):
        entries = [Entry(f'◾ 일정 {index}', '마감', 'm1', 2 + index) for index in range(5)]
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
                         [(datetime.date(2026, 8, 25), '◾ 도면 회신')])

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
        events = {datetime.date(2026, 9, 9): [Entry('◾ 지난 건', '마감', 'm1', 2)],
                  datetime.date(2026, 9, 11): [Entry('◾ 오늘 마감', '마감', 'm2', 3)],
                  datetime.date(2026, 9, 14): [Entry('▫ 착수', '시작', 'm3', 4),
                                               Entry('◾ 보고서', '마감', 'm3', 5)]}
        self.assertEqual([(day, entry.label) for day, entry in upcoming(events, self.TODAY)],
                         [(datetime.date(2026, 9, 11), '◾ 오늘 마감'),
                          (datetime.date(2026, 9, 14), '◾ 보고서')])
        self.assertEqual(describe(datetime.date(2026, 9, 9), self.TODAY), '2일 지남')
        self.assertEqual(describe(datetime.date(2026, 9, 11), self.TODAY), '오늘')

    def test_drawn_once_but_deadlines_refresh_every_export(self):
        sheet, book = MagicMock(), MagicMock()
        cells = {}
        # One mock per cell, or every write lands on the same object.
        sheet.Cells.side_effect = lambda row, col: cells.setdefault((row, col), MagicMock())
        book.Worksheets.side_effect = {DASHBOARD: sheet}.__getitem__
        events = {datetime.date(2026, 9, 14): [Entry('◾ 보고서', '마감', 'm3', 5)]}
        update_dashboard(book, events, self.TODAY, mail_rows={'m3': 7})
        sheet.Cells.Clear.assert_called_once()
        book.CustomDocumentProperties.return_value.Value = '1'
        update_dashboard(book, events, self.TODAY, mail_rows={'m3': 7})
        sheet.Cells.Clear.assert_called_once()                     # layout stays put
        self.assertEqual(cells[10, 8].Value, '2026-09-14')         # list still refilled
        self.assertEqual(cells[10, 9].Value, '◾ 보고서')
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
            stop.is_set.side_effect = [False, True]
            messages = []
            with patch('mail_assistant.worker.fetch_mail', return_value='새 메일 0건 수집'), patch('mail_assistant.worker.read_password', return_value='test'), patch('mail_assistant.worker.Excel') as excel:
                excel.return_value.update.side_effect = ExcelUpdateError('표 만들기', RuntimeError('invalid argument'))
                run({**CONFIG, 'workbook': str(directory / 'test.xlsx'), 'interval': 180}, directory, stop, messages.append)
            self.assertIn('표 만들기', messages[-1])
            self.assertIn('invalid argument', messages[-1])
            snapshot = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
            self.assertIn('invalid argument', snapshot['message'])
            store = Store(directory / 'mail.db')
            self.assertEqual(len(store.unexported(account_key(CONFIG))), 1)
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
