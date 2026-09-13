"""Sheet formatting. Applied once per style version, never on every export."""

def bgr(red, green, blue):
    """COM takes colours as BGR integers, which is easy to get backwards."""
    return (blue << 16) | (green << 8) | red


def parts(value):
    """BGR integer back to (red, green, blue)."""
    return value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF


def hex_rgb(value):
    """BGR integer to 'RRGGBB', for openpyxl."""
    return '{:02X}{:02X}{:02X}'.format(*parts(value))


def tk_color(value):
    """BGR integer to '#RRGGBB', for tkinter."""
    return '#' + hex_rgb(value)


def css_color(value):
    """The same '#RRGGBB', named so a call in the web page reads right."""
    return tk_color(value)


# Dashboard palette: dark. Data sheets and the calendar stay light.
DASH_BG = bgr(24, 24, 27)
DASH_CARD = bgr(39, 39, 46)
DASH_LINE = bgr(63, 63, 70)
DASH_TEXT = bgr(244, 244, 247)
DASH_MUTED = bgr(150, 152, 163)
ACCENT = bgr(255, 196, 0)
DASH_RED = bgr(248, 113, 113)
DASH_BLUE = bgr(96, 165, 250)
DASH_GREEN = bgr(74, 222, 128)
DASH_LINK = bgr(125, 185, 255)   # on the dark card
LINK = bgr(37, 99, 235)          # on the white data sheets

NEUTRAL = bgr(75, 85, 99)   # BGR: slate for '보통', between the status colours and CALM

HEADER_FILL = 0xEADFD4      # BGR: pale slate, the existing header colour
TODAY_FILL = 0xF7EBDE       # BGR: pale blue for today's calendar cell
URGENT = 0x0000C0           # BGR: deep red
SOON = 0x0070D0             # BGR: amber
CALM = 0x808080             # BGR: grey for low priority and side notes
LINE = 0xD0D0D0             # BGR: grid line grey
FONT = '맑은 고딕'
STYLE_VERSION = '3'
STYLE_PROPERTY = 'MailAssistantStyle'

CENTER, LEFT, TOP = -4108, -4131, -4160
EXPRESSION = 2              # xlExpression
CONTINUOUS, THIN = 1, 2     # xlContinuous, xlThin

# Conditional formats per sheet: (column range, formula, font colour, bold).
RULES = {
    '우선순위': [
        ('B2:B20000', '=$B2="긴급"', URGENT, True),
        ('B2:B20000', '=$B2="높음"', SOON, False),
        ('B2:B20000', '=$B2="낮음"', CALM, False),
    ],
    '일정': [
        # Deadlines are ISO text, so a plain text comparison orders them correctly.
        ('E2:E20000', '=AND(LEN($E2)>=10,LEFT($E2,10)<TEXT(TODAY(),"yyyy-mm-dd"))', URGENT, True),
        ('E2:E20000', '=AND(LEN($E2)>=10,LEFT($E2,10)>=TEXT(TODAY(),"yyyy-mm-dd"),'
                      'LEFT($E2,10)<=TEXT(TODAY()+3,"yyyy-mm-dd"))', SOON, False),
    ],
}


def read_marker(book, name):
    try:
        return str(book.CustomDocumentProperties(name).Value)
    except Exception:
        return ''


def write_marker(book, name, value):
    """Keep our bookkeeping in document properties, invisible to the user's sheets."""
    try:
        book.CustomDocumentProperties(name).Value = value
    except Exception:
        try:
            book.CustomDocumentProperties.Add(name, False, 4, value)  # msoPropertyTypeString
        except Exception:
            pass


def freeze_header(book, sheet):
    """FreezePanes is a window property, so the sheet has to be active for a moment."""
    active = book.ActiveSheet
    try:
        sheet.Activate()
        window = book.Windows(1)
        window.FreezePanes = False
        window.SplitRow, window.SplitColumn = 1, 0
        window.FreezePanes = True
    finally:
        try:
            active.Activate()
        except Exception:
            pass


def apply_rules(sheet, rules):
    # Clear only the columns we manage, so the user's own rules elsewhere survive.
    for address in dict.fromkeys(address for address, *_ in rules):
        sheet.Range(address).FormatConditions.Delete()
    for address, formula, color, bold in rules:
        # Named arguments: xlExpression takes no operator, and a positional None breaks it.
        condition = sheet.Range(address).FormatConditions.Add(Type=EXPRESSION, Formula1=formula)
        condition.Font.Color = color
        condition.Font.Bold = bold


def apply_style(book, sheets):
    if read_marker(book, STYLE_PROPERTY) == STYLE_VERSION:
        return
    for name, sheet in sheets.items():
        used = sheet.UsedRange
        used.Font.Name = FONT
        used.Font.Size = 10
        if name == '실행 상태':
            sheet.Columns(1).ColumnWidth = 26
            sheet.Columns(2).ColumnWidth = 62
            sheet.Columns(1).Font.Bold = True
            continue
        if sheet.ListObjects.Count:
            sheet.ListObjects(1).TableStyle = 'TableStyleLight8'
        header = sheet.Rows(1)
        header.Font.Bold = True
        header.Interior.Color = HEADER_FILL
        header.VerticalAlignment = TOP
        freeze_header(book, sheet)
        if name in RULES:
            apply_rules(sheet, RULES[name])
    write_marker(book, STYLE_PROPERTY, STYLE_VERSION)
