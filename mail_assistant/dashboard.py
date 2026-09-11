"""'대시보드' sheet: a dark summary screen driven by formulas.

The layout is pure data so the live COM writer and the openpyxl sample file
render exactly the same screen. Counts are formulas, so they follow the tables
as rows are appended; only the deadline list is refilled each export.
"""
from datetime import date, timedelta

from .core import HEADERS
from .style import (ACCENT, DASH_BG, DASH_BLUE, DASH_CARD, DASH_GREEN, DASH_LINE, DASH_LINK,
                    DASH_MUTED, DASH_RED,
                    DASH_TEXT, read_marker, write_marker)

SHEET = '대시보드'
DASHBOARD_PROPERTY = 'MailAssistantDashboard'
DASHBOARD_VERSION = '1'
CATEGORIES = ('업무 요청', '견적·계약', '회의·일정', '문의', '공지', '기타')
PRIORITIES = (('긴급', DASH_RED), ('높음', ACCENT), ('보통', DASH_TEXT), ('낮음', DASH_MUTED))
UPCOMING_ROWS = 6
UPCOMING_TOP = 10
WIDTHS = {1: 2.5, 2: 15, 3: 7, 4: 7, 5: 15, 6: 7, 7: 7, 8: 15, 9: 7, 10: 7,
          11: 14, 12: 9, 13: 30, 14: 10, 15: 2.5}


def column(sheet_name, header):
    """Full-column reference, e.g. '메일 목록'!I:I — no table names to keep in sync."""
    index = HEADERS[sheet_name].index(header)
    return f"'{sheet_name}'!{chr(65 + index)}:{chr(65 + index)}"


def deadline_range():
    index = HEADERS['일정'].index('마감')
    letter = chr(65 + index)
    return f"'일정'!${letter}$2:${letter}$20000"


def counted(formula):
    return f'={formula}'


def bar(count_cell, first, last):
    """In-cell bar scaled to the largest count, so it reads like a chart."""
    return (f'=IFERROR(IF({count_cell}=0,"",REPT("■",MAX(1,ROUND({count_cell}/'
            f'MAX({first}:{last})*14,0)))),"")')


def cards():
    deadlines = deadline_range()
    within = (f'SUMPRODUCT((LEN({deadlines})>=10)*(LEFT({deadlines},10)>=TEXT(TODAY(),"yyyy-mm-dd"))'
              f'*(LEFT({deadlines},10)<=TEXT(TODAY()+7,"yyyy-mm-dd")))')
    return [
        ('미처리 메일', counted(f'COUNTIF({column("메일 목록", "처리 상태")},"미처리")'), '전체 메일 목록 기준', ACCENT),
        ('긴급·높음', counted(f'COUNTIF({column("우선순위", "우선순위")},"긴급")+'
                           f'COUNTIF({column("우선순위", "우선순위")},"높음")'), '우선 처리 대상', DASH_RED),
        ('7일 내 마감', counted(within), '일정 시트의 마감 기준', DASH_BLUE),
        ('검토 전 초안', counted(f'COUNTIF({column("답변 초안", "검토 상태")},"검토 전")'), '답변 초안 확인 필요', DASH_GREEN),
    ]


def blocks(today=None):
    """[(row, column, width, kind, value, colour)] — the whole screen as data."""
    today = today or date.today()
    items = [
        (2, 2, 6, 'title', '메일 업무 도우미', ACCENT),
        (3, 2, 8, 'subtitle', '하이웍스 메일을 정리하고 엑셀에 반영합니다. 답변은 초안으로만 저장합니다.', DASH_MUTED),
        (2, 11, 2, 'label', '마지막 엑셀 반영', DASH_MUTED),
        (3, 11, 4, 'stamp', "='실행 상태'!B3", DASH_TEXT),
    ]
    for index, (label, formula, note, color) in enumerate(cards()):
        left = 2 + index * 3
        items.append((5, left, 3, 'card_label', label, DASH_MUTED))
        items.append((6, left, 3, 'card_value', formula, color))
        items.append((7, left, 3, 'card_note', note, DASH_MUTED))
    items.append((9, 2, 4, 'section', '메일 종류', DASH_TEXT))
    for index, name in enumerate(CATEGORIES):
        row = 10 + index
        items.append((row, 2, 1, 'item', name, DASH_TEXT))
        items.append((row, 3, 1, 'count', counted(f'COUNTIF({column("메일 목록", "종류")},"{name}")'), DASH_MUTED))
        items.append((row, 4, 2, 'bar', bar(f'$C${row}', '$C$10', f'$C${9 + len(CATEGORIES)}'), ACCENT))
    items.append((17, 2, 4, 'section', '우선순위', DASH_TEXT))
    for index, (name, color) in enumerate(PRIORITIES):
        row = 18 + index
        items.append((row, 2, 1, 'item', name, DASH_TEXT))
        items.append((row, 3, 1, 'count', counted(f'COUNTIF({column("우선순위", "우선순위")},"{name}")'), DASH_MUTED))
        items.append((row, 4, 2, 'bar', bar(f'$C${row}', '$C$18', f'$C${17 + len(PRIORITIES)}'), color))
    items.append((23, 2, 4, 'section', '최근 7일 수신', DASH_TEXT))
    for index in range(7):
        row = 24 + index
        stamp = f'TEXT(TODAY()-{6 - index},"yyyy-mm-dd")'
        items.append((row, 2, 1, 'item', f'={stamp}', DASH_MUTED))
        items.append((row, 3, 1, 'count', counted(f'COUNTIF({column("메일 목록", "수신 확인 시각")},{stamp}&"*")'), DASH_MUTED))
        items.append((row, 4, 2, 'bar', bar(f'$C${row}', '$C$24', '$C$30'), DASH_BLUE))
    items.append((9, 8, 6, 'section', '마감 임박', DASH_TEXT))
    # Rows UPCOMING_TOP..UPCOMING_TOP+UPCOMING_ROWS-1 are filled from the 일정 sheet.
    items.append((UPCOMING_TOP + UPCOMING_ROWS, 8, 6, 'card_note',
                  '일정 시트의 마감일 기준 · 매 반영마다 갱신', DASH_MUTED))
    items.append((18, 8, 6, 'section', '안내', DASH_TEXT))
    items.append((19, 8, 6, 'note', "='실행 상태'!B6", DASH_MUTED))
    items.append((21, 8, 6, 'card_note', '이 시트는 자동 생성됩니다. 값을 직접 고쳐도 다음 반영에서 되돌아갑니다.', DASH_MUTED))
    return items


def upcoming(events, today=None, limit=UPCOMING_ROWS):
    """Deadlines from today onwards, nearest first, as (date, entry) pairs."""
    today = today or date.today()
    rows = [(day, entry) for day in sorted(events) if day >= today
            for entry in events[day] if entry.kind != '시작']
    return rows[:limit]


def describe(day, today):
    left = (day - today).days
    if left == 0:
        return '오늘'
    return f'{left}일 뒤' if left > 0 else f'{-left}일 지남'


def fill_upcoming(sheet, events, today, mail_rows=None):
    rows = upcoming(events, today)
    mail_rows = mail_rows or {}
    for index in range(UPCOMING_ROWS):
        row = UPCOMING_TOP + index
        date_cell = sheet.Cells(row, 8)
        name_cell = sheet.Cells(row, 9)
        left_cell = sheet.Cells(row, 12)
        for cell in (date_cell, name_cell, left_cell):
            cell.NumberFormat = '@'
        name_cell.Hyperlinks.Delete()
        if index < len(rows):
            day, entry = rows[index]
            date_cell.Value = day.isoformat()
            name_cell.Value = entry.label
            left_cell.Value = describe(day, today)
            left_cell.Font.Color = DASH_RED if day - today <= timedelta(days=3) else DASH_MUTED
            link(sheet, name_cell, mail_rows.get(entry.mail_id), entry.row, entry.label)
        else:
            date_cell.Value = name_cell.Value = left_cell.Value = ''
            name_cell.Font.Color = DASH_TEXT
        date_cell.Font.Color = DASH_MUTED


def link(sheet, cell, mail_row, schedule_row, text):
    """Jump to the mail row when we know it, otherwise to the 일정 line."""
    target = f"'메일 목록'!A{mail_row}" if mail_row else f"'일정'!A{schedule_row}"
    tip = '메일 목록에서 이 메일 보기' if mail_row else '일정 시트에서 이 줄 보기'
    sheet.Hyperlinks.Add(Anchor=cell, Address='', SubAddress=target, TextToDisplay=text, ScreenTip=tip)
    cell.Font.Color = DASH_LINK
    cell.Font.Underline = 2  # xlUnderlineStyleSingle


SIZES = {'title': (20, True), 'subtitle': (10, False), 'label': (9, False), 'stamp': (10, False),
         'card_label': (9, False), 'card_value': (22, True), 'card_note': (8, False),
         'section': (12, True), 'item': (10, False), 'count': (10, True), 'bar': (10, False),
         'note': (10, False)}


def draw(sheet, book):
    """Paint the whole screen. Cheap enough to redo when the version changes."""
    from .style import FONT
    sheet.Cells.Clear()
    sheet.Cells.Interior.Color = DASH_BG
    sheet.Cells.Font.Name = FONT
    sheet.Cells.Font.Color = DASH_TEXT
    for index, width in WIDTHS.items():
        sheet.Columns(index).ColumnWidth = width
    for row, height in ((1, 10), (2, 30), (3, 18), (4, 8), (5, 16), (6, 34), (7, 14), (8, 10)):
        sheet.Rows(row).RowHeight = height
    for row, first, width, kind, value, color in blocks():
        target = sheet.Range(sheet.Cells(row, first), sheet.Cells(row, first + width - 1))
        if width > 1:
            target.Merge()
        cell = sheet.Cells(row, first)
        size, bold = SIZES[kind]
        cell.Font.Size = size
        cell.Font.Bold = bold
        cell.Font.Color = color
        cell.VerticalAlignment = -4108  # xlCenter
        if str(value).startswith('='):
            cell.Formula = value
        else:
            cell.NumberFormat = '@'
            cell.Value = value
        if kind in ('card_label', 'card_value', 'card_note'):
            cell.Interior.Color = DASH_CARD
    for index in range(len(cards())):
        left = 2 + index * 3
        card = sheet.Range(sheet.Cells(5, left), sheet.Cells(7, left + 2))
        card.Interior.Color = DASH_CARD
        card.Borders.Color = DASH_LINE
        card.Borders.LineStyle = 1
    panel = sheet.Range(sheet.Cells(9, 8), sheet.Cells(UPCOMING_TOP + UPCOMING_ROWS, 13))
    panel.Interior.Color = DASH_CARD


def update_dashboard(book, events, today=None, mail_rows=None):
    today = today or date.today()
    try:
        sheet = book.Worksheets(SHEET)
    except Exception:
        sheet = book.Worksheets.Add(Before=book.Worksheets(1))
        sheet.Name = SHEET
    if read_marker(book, DASHBOARD_PROPERTY) != DASHBOARD_VERSION:
        draw(sheet, book)
        write_marker(book, DASHBOARD_PROPERTY, DASHBOARD_VERSION)
    fill_upcoming(sheet, events, today, mail_rows)
    try:
        sheet.Activate()
        book.Windows(1).DisplayGridlines = False
    except Exception:
        pass
