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

# TDS — toss_design.md 의 팔레트. 저기에는 OKLCH 로 적혀 있고 여기 있는 것은 그것을
# sRGB 로 옮긴 값이다. 옮겨 오는 이유는 하나다: Excel COM 은 BGR 정수만 받고 화면은
# css_color() 로 같은 정수를 읽으므로, 두 매체의 색이 갈라질 자리가 여기 말고는 없다.
# 문서 자신이 "토큰 패키지가 공개되지 않아 이 값들은 산문 위에 서 있다"고 적어 두었다 —
# 실제 토스 렌더값과 다를 수 있고, 다르다는 것이 확인되면 고칠 곳도 여기 한 곳뿐이다.
# 브랜드 파랑 셋만은 문서의 OKLCH 가 아니라 실제 렌더값에서 온다. 문서 자신이 이 절을
# 두고 "값을 그대로 쓰려는 사용자는 실제 토스 서비스에서 렌더값을 확인하는 편이 안전하다"고
# 적어 두었고, 확인해 보니 정말 달랐다 — oklch(0.624 0.176 254)를 sRGB 로 옮기면
# #2887EE 가 나오는데 이것은 실제 토스 블루보다 밝고 청록 쪽으로 기운 색이다.
TDS_BLUE_500 = bgr(49, 130, 246)    # #3182F6 — 화면당 하나의 primary CTA
TDS_BLUE_600 = bgr(27, 100, 218)    # #1B64DA — pressed
TDS_BLUE_50 = bgr(232, 243, 255)    # #E8F3FF — brand-weak 바탕
# 이 한 단계는 문서에 없고 여기서 뽑은 것이다 — toss_design.md 는 브랜드 블루의 step
# 을 50/500/600/700 만 내놓고 "blue-100~400 의 OKLCH 는 surface 되지 않았다"고 적어
# 두었다. blue-500 이 실제로 그려지는 색조(258°)를 그대로 잡고 L 만 0.76 으로 올린
# 값이며, 문서가 차트에 허용하는 유일한 색이 브랜드 블루 하나인 것(§막대형 데이터
# 시각화)이 단계를 하나 더 뽑는 쪽이 두 번째 색을 들이는 쪽보다 나은 이유다.
TDS_BLUE_300 = bgr(137, 179, 241)   # #89B3F1 — oklch(0.760 0.100 258)
# 회색은 cool-blue 가 섞여 있다. 토스는 순검정을 쓰지 않는다.
TDS_GREY_900 = bgr(20, 31, 44)      # oklch(0.234 0.030 254) — 본문
TDS_GREY_800 = bgr(45, 58, 72)      # oklch(0.342 0.030 253)
TDS_GREY_700 = bgr(75, 87, 101)     # oklch(0.452 0.028 253) — 보조 본문
TDS_GREY_600 = bgr(106, 116, 128)   # oklch(0.555 0.022 253)
TDS_GREY_500 = bgr(135, 145, 156)   # oklch(0.652 0.020 252) — 곁줄
TDS_GREY_400 = bgr(167, 176, 185)   # oklch(0.752 0.016 251) — disabled, 강한 선
TDS_GREY_300 = bgr(197, 203, 210)   # oklch(0.840 0.012 248)
TDS_GREY_200 = bgr(222, 227, 231)   # oklch(0.913 0.008 247) — 기본 헤어라인
TDS_GREY_150 = bgr(224, 228, 232)   # oklch(0.918 0.007 247) — 카드 안의 더 연한 줄
TDS_GREY_100 = bgr(238, 241, 244)   # oklch(0.957 0.005 247) — 보조 표면, 입력칸 바탕
TDS_GREY_50 = bgr(246, 248, 250)    # oklch(0.978 0.003 247)
TDS_RED_500 = bgr(240, 56, 72)      # oklch(0.628 0.218 22)
TDS_GREEN_500 = bgr(0, 119, 56)     # oklch(0.493 0.143 154)
TDS_ORANGE_500 = bgr(255, 136, 0)   # oklch(0.748 0.183 56)
TDS_NAVY_900 = bgr(1, 10, 37)       # oklch(0.155 0.060 261) — 그림자와 scrim 의 밑색

# 화면과 밝은 시트가 함께 쓰는 이름들. 값은 위의 TDS 팔레트를 가리킨다 — 이름은 역할이고
# 값은 시스템이 정한다. 위의 DASH_* 는 어두운 대시보드 시트 전용이라 따라가지 않는다:
# TDS_GREEN_500 은 깊은 초록이어서 검은 카드 위에서 읽히지 않는다.
NEUTRAL = TDS_GREY_700      # '보통' — 상태색과 CALM 사이
LINK = TDS_BLUE_500         # 밝은 데이터 시트 위의 파랑, 그리고 화면의 강조색
URGENT = TDS_RED_500
SOON = TDS_ORANGE_500
CALM = TDS_GREY_500         # 낮은 우선순위와 곁줄
LINE = TDS_GREY_200         # 격자선
OK = TDS_GREEN_500          # 화면의 '됐다'. 어두운 시트는 DASH_GREEN 을 계속 쓴다

HEADER_FILL = 0xEADFD4      # BGR: pale slate, the existing header colour
TODAY_FILL = 0xF7EBDE       # BGR: pale blue for today's calendar cell
FONT = '맑은 고딕'
STYLE_VERSION = '4'
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
