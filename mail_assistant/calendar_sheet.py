"""'일정 달력' sheet: the 일정 rows redrawn as a month grid.

The sheet is generated, never edited by hand, so each refresh redraws it whole.
Pure helpers build the text and the colour spans; only draw_* touches COM.
"""
import calendar as stdlib_calendar
import hashlib
import json
import re
from collections import namedtuple
from datetime import date, timedelta

from .style import (CALM, CENTER, CONTINUOUS, HEADER_FILL, LEFT, LINE, LINK, THIN, TODAY_FILL,
                    TOP, URGENT, read_marker, write_marker)

SHEET = '일정 달력'
CALENDAR_PROPERTY = 'MailAssistantCalendar'
WEEKDAYS = ('월', '화', '수', '목', '금', '토', '일')
DAY_DATE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
DAY_TIME = re.compile(r'^\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2})')
# Three geometric shapes of the same cast, so a cell can say which kind it is without
# colour. The old ◾/▫/· were three different sizes — a middle dot beside a filled
# square reads as a glyph the font failed to draw rather than as a second kind.
MARKERS = {'마감': '■', '시작': '▶', '확인 필요': '◆'}
# 시작 is blue, not the amber it used to share with '높음': beside URGENT's deep red an
# amber block is the same colour at a glance, and the calendar is where the two sit
# side by side. Red / blue / grey are three hues, not three reds.
COLORS = {'마감': URGENT, '시작': LINK, '확인 필요': CALM}
PER_DAY = 3
# evidence and clock both default, so the four-argument construction in older code and
# tests keeps working. clock is the entry's own time, kept beside the label because the
# web calendar needs it as a number ('14:00' in a timeGrid slot) and not as text.
Entry = namedtuple('Entry', 'label kind mail_id row evidence clock')
Entry.__new__.__defaults__ = ('', '')


def parse_day(value):
    """ISO date, optionally with a time. Anything else is not a calendar entry."""
    text = str(value or '').strip()
    match = DAY_DATE.match(text)
    if not match:
        return None, ''
    try:
        day = date(*(int(part) for part in match.groups()))
    except ValueError:
        return None, ''
    clock = DAY_TIME.match(text)
    return day, clock.group(1) if clock else ''


def plain_title(label):
    """An entry label without the marker collect() puts on the front of it.

    Kept beside MARKERS rather than in a screen: ■/▶/◆ are how an Excel cell says
    마감·시작·확인 필요 in one colour of text, and everything that is not that cell —
    the web panels, the tooltip, the briefing payload — draws the kind some other way
    and would otherwise say it twice.
    """
    text = str(label or '')
    if text[:1] in MARKERS.values():
        text = text[1:].lstrip()
    return text


def collect(rows, first_row=2):
    """일정 rows -> {date: [Entry]}, sorted by time inside each day.

    first_row is the sheet row of rows[0], so an entry can point back at its line.
    """
    events = {}
    for offset, row in enumerate(rows):
        title = str(row[2] or '').strip() or '(제목 없음)'
        review = str(row[6] or '').strip()
        mail_id = str(row[1] or '').strip()
        evidence = str(row[5] or '').strip() if len(row) > 5 else ''
        for index, kind in ((4, '마감'), (3, '시작')):
            day, clock = parse_day(row[index] if len(row) > index else '')
            if not day:
                continue
            if review:
                kind = '확인 필요'
            label = f'{MARKERS[kind]} {clock} {title}'.replace('  ', ' ').strip()
            events.setdefault(day, []).append(
                (clock, Entry(label, kind, mail_id, first_row + offset, evidence, clock)))
    return {day: [entry for _, entry in sorted(entries, key=lambda pair: (pair[0], pair[1].label))]
            for day, entries in events.items()}


def cell_text(day, entries):
    """Day number then one line per entry, with the colour span of each line."""
    shown = entries[:PER_DAY]
    lines = [str(day.day)] + [entry.label for entry in shown]
    if len(entries) > PER_DAY:
        lines.append(f'…외 {len(entries) - PER_DAY}건')
    spans = []
    start = len(lines[0]) + 2  # 1-based, past the day number and its newline
    for entry in shown:
        spans.append((start, len(entry.label), COLORS[entry.kind]))
        start += len(entry.label) + 1
    return '\n'.join(lines), spans


def weeks_of(year, month):
    """Six Monday-first weeks of dates covering the month."""
    return stdlib_calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)[:6]


def month_grid(year, month, events):
    """(6x7 text rows, {(row, column): (day, spans)}); days of other months stay blank."""
    weeks = weeks_of(year, month)
    text = [['' for _ in WEEKDAYS] for _ in range(6)]
    cells = {}
    for week_index, week in enumerate(weeks):
        for day_index, day in enumerate(week):
            if day.month != month:
                continue
            body, marks = cell_text(day, events.get(day, []))
            text[week_index][day_index] = body
            cells[week_index, day_index] = (day, marks)
    return text, cells


def overdue(events, today):
    """Deadlines before this month, which the two-month grid cannot show."""
    start = today.replace(day=1)
    return [(day, entry.label) for day in sorted(events) if day < start
            for entry in events[day] if entry.kind != '시작'][:10]


def next_month(today):
    return (today.replace(day=28) + timedelta(days=7)).replace(day=1)


def signature(events, today):
    payload = {day.isoformat(): [entry.label for entry in entries] for day, entries in sorted(events.items())}
    payload['today'] = today.isoformat()
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def read_rows(sheet, width):
    last = max(1, sheet.Cells(sheet.Rows.Count, 1).End(-4162).Row)
    if last < 2:
        return []
    return list(sheet.Range(sheet.Cells(2, 1), sheet.Cells(last, width)).Value)


def draw_month(sheet, top, year, month, events, today):
    title = sheet.Cells(top, 1)
    title.NumberFormat = '@'
    title.Value = f'{year}년 {month}월'
    title.Font.Bold = True
    title.Font.Size = 13
    header = sheet.Range(sheet.Cells(top + 1, 1), sheet.Cells(top + 1, 7))
    header.NumberFormat = '@'
    header.Value = (WEEKDAYS,)
    header.Font.Bold = True
    header.Interior.Color = HEADER_FILL
    header.HorizontalAlignment = CENTER
    text, cells = month_grid(year, month, events)
    grid = sheet.Range(sheet.Cells(top + 2, 1), sheet.Cells(top + 7, 7))
    grid.NumberFormat = '@'   # Titles such as '=회의' must stay literal text.
    grid.Value = tuple(tuple(row) for row in text)
    grid.WrapText = True
    grid.VerticalAlignment = TOP
    grid.HorizontalAlignment = LEFT
    grid.Borders.LineStyle = CONTINUOUS
    grid.Borders.Weight = THIN
    grid.Borders.Color = LINE
    for row in range(top + 2, top + 8):
        sheet.Rows(row).RowHeight = 76
    for (week_index, day_index), (day, marks) in cells.items():
        if not marks and day != today and day_index < 5:
            continue  # Nothing to colour in this cell.
        cell = sheet.Cells(top + 2 + week_index, day_index + 1)
        for start, length, color in marks:
            cell.Characters(start, length).Font.Color = color
        number = len(str(day.day))
        if day_index >= 5:
            cell.Characters(1, number).Font.Color = CALM
        if day == today:
            cell.Interior.Color = TODAY_FILL
            cell.Characters(1, number).Font.Bold = True


def legend_text():
    """The legend line, and where each marker sits in it.

    The offsets are counted off the text rather than assumed: a fixed stride coloured
    whichever character happened to be there, which after the markers changed width
    would have tinted a syllable of the label instead of the mark in front of it.
    """
    parts, spans, at = [], [], 1
    for kind in ('마감', '시작', '확인 필요'):
        spans.append((at, COLORS[kind]))
        piece = f'{MARKERS[kind]} {kind}    '
        parts.append(piece)
        at += len(piece)
    return ''.join(parts) + '    일정 시트를 매 반영마다 다시 그립니다.', spans


def draw_notes(sheet, row, events, today):
    legend = sheet.Cells(row, 1)
    legend.NumberFormat = '@'
    text, spans = legend_text()
    legend.Value = text
    for at, color in spans:
        legend.Characters(at, 1).Font.Color = color
    past = overdue(events, today)
    if not past:
        return
    heading = sheet.Cells(row + 2, 1)
    heading.NumberFormat = '@'
    heading.Value = f'지난 마감 {len(past)}건 (달력 범위 밖)'
    heading.Font.Bold = True
    for index, (day, label) in enumerate(past, row + 3):
        cell = sheet.Cells(index, 1)
        cell.NumberFormat = '@'
        cell.Value = f'{day.isoformat()}  {label}'
        cell.Font.Color = CALM


def sheet_events(sheet, width):
    return collect(read_rows(sheet, width))


def update_calendar(book, events, today=None):
    """Redraw the month grid when the 일정 rows or the date changed."""
    today = today or date.today()
    try:
        schedule = book.Worksheets('일정')
    except Exception:
        return  # Nothing analysed yet, so there is no calendar to draw.
    stamp = signature(events, today)
    if read_marker(book, CALENDAR_PROPERTY) == stamp:
        return
    try:
        sheet = book.Worksheets(SHEET)
    except Exception:
        sheet = book.Worksheets.Add(After=schedule)
        sheet.Name = SHEET
    sheet.Cells.Clear()
    draw_month(sheet, 1, today.year, today.month, events, today)
    following = next_month(today)
    draw_month(sheet, 10, following.year, following.month, events, today)
    draw_notes(sheet, 19, events, today)
    for column in range(1, 8):
        sheet.Columns(column).ColumnWidth = 21
    write_marker(book, CALENDAR_PROPERTY, stamp)
