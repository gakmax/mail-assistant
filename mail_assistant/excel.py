"""Desktop Excel adapter. Only the owning thread may access COM objects."""
import re
from pathlib import Path
from urllib.parse import quote

from .calendar_sheet import sheet_events, update_calendar
from .dashboard import update_dashboard
from .core import HEADERS, address_of, workbook_rows
from .style import apply_style


def error_detail(exc):
    """Keep COM's useful description/code, without dumping arguments or mail rows."""
    if isinstance(exc, ExcelUpdateError):
        return str(exc)
    info = getattr(exc, 'excepinfo', None)
    description = info[2] if info and len(info) > 2 and info[2] else str(exc)
    codes = []
    hresult = getattr(exc, 'hresult', None)
    if isinstance(hresult, int):
        codes.append(f'0x{hresult & 0xffffffff:08X}')
    if info and len(info) > 5 and isinstance(info[5], int) and info[5]:
        codes.append(f'0x{info[5] & 0xffffffff:08X}')
    code = f" [{', '.join(codes)}]" if codes else ''
    return f'{type(exc).__name__}{code}: {str(description).strip()[:700]}'


class ExcelUpdateError(RuntimeError):
    def __init__(self, stage, cause):
        super().__init__(f'{stage} — {error_detail(cause)}')


def column_name(index):
    name = ''
    while True:
        index, remainder = divmod(index, 26)
        name = chr(65 + remainder) + name
        if not index:
            return name
        index -= 1


GENERATED_HEADER = re.compile(r'^(Column|열)\s*\d+$')


def row_text(sheet, row, width):
    values = sheet.Range(sheet.Cells(row, 1), sheet.Cells(row, width)).Value[0]
    return ['' if value is None else str(value).strip() for value in values]


def repair_generated_header(sheet, head, headers):
    """Excel writes its own 'Column1..N' header row when it creates a table without
    recognising ours, pushing the real names one row down. Put them back rather than
    failing on every later export."""
    width = len(headers)
    if not all(GENERATED_HEADER.match(value) for value in row_text(sheet, 1, width)):
        return False
    duplicated = row_text(sheet, 2, width) == headers
    head.NumberFormat = '@'
    head.Value = (tuple(headers),)
    if duplicated:
        # Row 2 holds nothing but the displaced header text, never a mail row.
        sheet.Rows(2).Delete()
    return True


def ensure_table(sheet, head, width):
    if sheet.ListObjects.Count:
        return
    # Header row plus one more row: a single-row source invites a wrong header guess.
    source = sheet.Range(sheet.Cells(1, 1), sheet.Cells(2, width))
    try:
        # Named arguments omit LinkSource and pin xlYes. A positional Missing can shift
        # the header flag, and Excel then invents its own 'Column1..N' header row.
        sheet.ListObjects.Add(SourceType=1, Source=source, XlListObjectHasHeaders=1)
    except Exception:
        if not sheet.ListObjects.Count:
            sheet.ListObjects.Add(1, source)


def first_column(value):
    """Range.Value as a flat list.

    COM hands back a tuple of rows for a multi-cell range, a bare scalar for a single
    cell and None for a single empty cell. Without this, a sheet holding exactly one
    data row raised 'NoneType is not iterable' — or, worse, iterated the characters of
    a one-cell string and compared ids against letters.
    """
    if value is None:
        return []
    if not isinstance(value, tuple):
        return [value]
    return [item[0] if isinstance(item, tuple) else item for item in value]


def append_missing(sheet, rows, width):
    last = max(1, sheet.Cells(sheet.Rows.Count, 1).End(-4162).Row)
    existing = set()
    if last > 1:
        values = sheet.Range(sheet.Cells(2, 1), sheet.Cells(last, 1)).Value
        existing = {str(item) for item in first_column(values) if item is not None}
    written = []
    for row in rows:
        if str(row[0]) in existing:
            continue
        last += 1
        target = sheet.Range(sheet.Cells(last, 1), sheet.Cells(last, width))
        # Mail text such as '=HYPERLINK(...)' must remain literal text.
        target.NumberFormat = '@'
        target.Value = (tuple(str(value or '')[:32700] for value in row),)
        existing.add(str(row[0]))
        written.append((last, row))
    return written


def mail_rows(sheet):
    """{메일 ID: 행 번호} for the links that jump to a mail."""
    last = max(1, sheet.Cells(sheet.Rows.Count, 1).End(-4162).Row)
    if last < 2:
        return {}
    values = sheet.Range(sheet.Cells(2, 1), sheet.Cells(last, 1)).Value
    return {str(item): index for index, item in enumerate(first_column(values), 2)
            if item is not None}


MAILTO_LIMIT = 1800   # Excel rejects hyperlinks much beyond 2,000 characters.


def mailto(sender, subject, draft):
    """A mail compose link with the draft filled in, or '' when there is no address."""
    address = address_of(sender)
    if not address:
        return ''
    link = f'mailto:{address}?subject={quote(str(subject or ""))}'
    body = quote(str(draft or ''))
    room = MAILTO_LIMIT - len(link) - len('&body=')
    return f'{link}&body={body[:max(0, room)]}' if room > 0 else link


def link_to_draft(sheet, name, row, values):
    """답변 제목 셀에서 초안이 채워진 메일 작성 창을 엽니다."""
    address = mailto(values[1], values[2], values[3])
    if not address:
        return
    column = HEADERS[name].index('답변 제목') + 1
    cell = sheet.Cells(row, column)
    sheet.Hyperlinks.Add(Anchor=cell, Address=address, TextToDisplay=str(cell.Value),
                         ScreenTip='기본 메일 앱에서 초안이 채워진 답장 창 열기')


def link_to_mail(sheet, name, row, mail_row):
    """메일 ID 셀에서 메일 목록의 해당 행으로 이동."""
    column = HEADERS[name].index('메일 ID') + 1
    cell = sheet.Cells(row, column)
    sheet.Hyperlinks.Add(Anchor=cell, Address='', SubAddress=f"'메일 목록'!A{mail_row}",
                         TextToDisplay=str(cell.Value), ScreenTip='메일 목록에서 이 메일 보기')


class Excel:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def update(self, rows, status):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        app = book = None
        owned = False
        stage = '열린 엑셀 찾기'
        primary_error = None
        try:
            # Enumerate file monikers to find the correct workbook across Excel instances.
            rot = pythoncom.GetRunningObjectTable()
            context = pythoncom.CreateBindCtx(0)
            for moniker in rot.EnumRunning():
                try:
                    name = moniker.GetDisplayName(context, None)
                    if str(Path(name).resolve()).casefold() == str(self.path).casefold():
                        book = win32com.client.Dispatch(rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
                        app = book.Application
                        break
                except (pythoncom.com_error, OSError, ValueError):
                    continue
            if book is None:
                stage = 'Microsoft Excel 실행'
                app = win32com.client.DispatchEx('Excel.Application')
                owned = True
                app.DisplayAlerts = False
                app.AutomationSecurity = 3  # Disable macros in programmatically opened files.
                if self.path.exists():
                    stage = '엑셀 파일 열기'
                    book = app.Workbooks.Open(str(self.path), UpdateLinks=0, ReadOnly=False)
                else:
                    stage = '엑셀 파일 만들기'
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    book = app.Workbooks.Add()
            stage = '엑셀 편집 가능 여부 확인'
            if book.ReadOnly:
                raise RuntimeError('대상 파일이 읽기 전용이에요. 다른 Excel 창의 파일 잠금이나 파일 권한을 확인해 주세요.')
            if not app.Ready:
                raise RuntimeError('Excel이 작업 중이에요. 셀 입력·대화상자를 마치면 다시 반영해요.')
            stage = '시트 및 셀 쓰기'
            self._write(book, rows, status)
            stage = '엑셀 파일 저장'
            if not self.path.exists():
                book.SaveAs(str(self.path), FileFormat=51)
            else:
                book.Save()
        except Exception as exc:
            primary_error = exc
            if isinstance(exc, ExcelUpdateError):
                raise
            raise ExcelUpdateError(stage, exc) from exc
        finally:
            # Never close or quit a user's Excel instance.
            cleanup_error = None
            try:
                if owned and app is not None:
                    try:
                        if book is not None:
                            book.Close(SaveChanges=False)
                    except Exception as exc:
                        cleanup_error = exc
                    try:
                        app.Quit()
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                book = app = None
                pythoncom.CoUninitialize()
            if cleanup_error is not None and primary_error is None:
                raise ExcelUpdateError('백그라운드 엑셀 닫기', cleanup_error) from cleanup_error

    @staticmethod
    def _write(book, rows, status):
        sheets = {}
        for name, headers in HEADERS.items():
            try:
                sheet = book.Worksheets(name)
            except Exception:
                sheet = book.Worksheets.Add(After=book.Worksheets(book.Worksheets.Count))
                sheet.Name = name
            head = sheet.Range(sheet.Cells(1, 1), sheet.Cells(1, len(headers)))
            repair_generated_header(sheet, head, headers)
            current = list(head.Value[0])
            # Compare on trimmed text: spacing or cell formatting must not stop the export.
            trimmed = ['' if value is None else str(value).strip() for value in current]
            if any(trimmed) and trimmed != headers:
                # Name every offending column so the sheet can be repaired without guessing.
                diff = '; '.join(f'{column_name(i)}1: 현재 "{c}" / 기대 "{h}"'
                                 for i, (c, h) in enumerate(zip(trimmed, headers)) if c != h)
                raise RuntimeError(f'{name} 시트의 열 구성이 바뀌었어요. 가이드의 열 순서를 되돌려 주세요. ({diff})')
            if any(trimmed) and current != headers:
                head.Value = (tuple(headers),)  # Same columns, cosmetic difference only.
            if not any(trimmed):
                head.Value = (tuple(headers),)
                head.Font.Bold = True
                head.Interior.Color = 0xEADFD4
                sheet.Columns(1).ColumnWidth = 27
                for column in range(2, len(headers) + 1):
                    sheet.Columns(column).ColumnWidth = 24 if column < 5 else 48
                head.WrapText = True
                head.VerticalAlignment = -4160
            if name != '실행 상태':
                try:
                    # Recover when a previous attempt wrote headers but failed to create the table.
                    ensure_table(sheet, head, len(headers))
                except Exception as exc:
                    raise ExcelUpdateError(f'{name} 시트의 표 만들기', exc) from exc
                # Undo a header row Excel may have just invented for the new table.
                repair_generated_header(sheet, head, headers)
            sheets[name] = sheet
        added = []
        for row in rows:
            for name, values in workbook_rows(row).items():
                try:
                    for line, written in append_missing(sheets[name], values, len(HEADERS[name])):
                        added.append((name, line, row['id'], written))
                except Exception as exc:
                    raise ExcelUpdateError(f'{name} 시트의 행 추가', exc) from exc
        for name, sheet in sheets.items():
            if name != '실행 상태' and sheet.ListObjects.Count:
                last = max(2, sheet.Cells(sheet.Rows.Count, 1).End(-4162).Row)
                area = sheet.Range(sheet.Cells(1, 1), sheet.Cells(last, len(HEADERS[name])))
                area.WrapText = True
                area.VerticalAlignment = -4160
                sheet.ListObjects(1).Resize(area)
        # Cosmetic work must never lose an export: report it instead of raising.
        notes = []
        events = {}
        try:
            events = sheet_events(sheets['일정'], len(HEADERS['일정']))
        except Exception as exc:
            notes.append(('일정 읽기', '실패: ' + error_detail(exc)))
        index = {}
        try:
            index = mail_rows(sheets['메일 목록'])
        except Exception as exc:
            notes.append(('메일 행 찾기', '실패: ' + error_detail(exc)))
        for stage, action in (('서식 적용', lambda: apply_style(book, sheets)),
                              ('메일 이동 링크', lambda: [link_to_mail(sheets[name], name, line, index[ident])
                                                    for name, line, ident, _ in added
                                                    if name != '메일 목록' and ident in index]),
                              ('답장 링크', lambda: [link_to_draft(sheets[name], name, line, written)
                                                 for name, line, _, written in added
                                                 if name == '답변 초안']),
                              ('일정 달력 갱신', lambda: update_calendar(book, events)),
                              ('대시보드 갱신', lambda: update_dashboard(book, events, mail_rows=index))):
            try:
                action()
            except Exception as exc:
                notes.append((stage, '실패: ' + error_detail(exc)))
        sheet = sheets['실행 상태']
        for index, (key, value) in enumerate(list(status.items()) + notes, 2):
            target = sheet.Range(sheet.Cells(index, 1), sheet.Cells(index, 2))
            target.NumberFormat = '@'
            target.Value = ((key, str(value)),)
