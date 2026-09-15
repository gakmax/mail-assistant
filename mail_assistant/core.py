from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path


KST = timezone(timedelta(hours=9))
HANDLED = '처리'
# What tells a free-standing 상담 room's key from a mail's. A mail id is 24 hex
# characters, so '#' can never be one.
ROOM_MARK = '#'
# 직접 추가한 일정's id, in the places that hold a mail id. A mail id is 24 hex
# characters, so neither marker can ever collide with one — the same trick, and
# the same reason: one column, two kinds of owner, and no way to confuse them.
EVENT_MARK = '@'
PROGRESS = '진행'      # the kanban's middle column; '' -> 진행 -> 처리
FAILED = '실패'
# Set by the worker while Codex is actually looking at a mail, cleared the moment it
# is not. One process at a time (services.codex_slot), but that one call may be reading
# a batch, so every row in it carries the marker until the answer comes back.
ANALYZING = '분석 중'
# 답장이 필요하다고 분석이 판단했는데, 받은 지 WAIT_DAYS일이 지나도록 완료 표시가
# 없는 메일. 이것은 state_of()가 돌려주는 '상태'가 아니라 미처리를 시간으로 좁힌
# *걸러보기*다 — 목록의 상태 칸은 그대로 '미처리'라고 말한다. 그것이 사실이기도 하고,
# 이 앱은 보낸 메일을 볼 수 없어서 '답장을 안 했다'고는 끝내 말할 수 없기 때문이다.
# 아는 것은 '완료 표시가 없다'까지이고, 화면도 딱 그만큼만 말한다.
WAITING = '답장 대기'
# 이틀. 업무 메일에서 하루는 아직 '오늘 안에'이고, 사흘은 이미 늦은 뒤다. 문턱이
# 있는 이유는 노이즈를 줄이는 것보다 카드가 0으로 떨어질 수 있게 하는 데 있다 —
# 어제 온 메일까지 세면 이 숫자는 영영 내려오지 않고, 내려오지 않는 숫자는 읽는
# 사람이 보지 않게 되는 숫자다(분석 실패 카드가 0일 때 아예 없는 것과 같은 계산).
WAIT_DAYS = 2
LOG_SCHEMA = 'CREATE TABLE IF NOT EXISTS log (at TEXT NOT NULL, text TEXT NOT NULL)'
LOG_TRIM_EVERY = 50
LIST_LIMIT = 50
# Kept here rather than imported from dashboard, which imports this module. A test
# asserts the two agree.
PRIORITY_ORDER = ('긴급', '높음', '보통', '낮음')


# retry_at for a mail no clock should ever bring back: one that failed for a reason
# asking again cannot change. Far-future rather than a flag column, because pending()
# already compares this against time.time() and 다시 분석 (Store.reset) writes 0 over it,
# which is the one way back in.
NO_RETRY = 4102444800.0     # 2100-01-01
# What parse_mail() puts where a Subject header should have been. Named because
# services.analyze() has to tell it apart from a subject somebody actually wrote:
# a mail with neither a subject nor a body has nothing to analyse at all.
NO_SUBJECT = '(제목 없음)'


def event_key(ident):
    """A 직접 추가한 일정's id, in the fields that otherwise hold a mail id."""
    return f'{EVENT_MARK}{ident}'


def is_event_key(value):
    """직접 추가한 일정 rather than a mail's. A mail id is 24 hex characters."""
    return str(value or '').startswith(EVENT_MARK)


def event_row_id(value):
    """The event table's own id behind a key, or None when the key is a mail's."""
    if not is_event_key(value):
        return None
    try:
        return int(str(value)[len(EVENT_MARK):])
    except ValueError:
        return None


def sql_text(value):
    """A literal for our own constants only. User input always goes through a parameter."""
    if "'" in value:
        raise ValueError(f'SQL 리터럴에 쓸 수 없는 값입니다: {value!r}')
    return "'" + value + "'"


def state_case():
    """state_of() as SQL, so '상태' can be sorted without reading every row."""
    return (f'CASE WHEN handled = {sql_text(HANDLED)} THEN 5 '
            f'WHEN handled = {sql_text(PROGRESS)} THEN 4 '
            "WHEN result IS NOT NULL THEN 3 WHEN analyzing <> '' THEN 1 "
            'WHEN attempts > 0 THEN 2 ELSE 0 END')


def priority_case():
    """긴급 first. Alphabetical order means nothing for these four words."""
    whens = ' '.join(f'WHEN {sql_text(name)} THEN {rank}'
                     for rank, name in enumerate(PRIORITY_ORDER))
    return f'CASE priority {whens} ELSE {len(PRIORITY_ORDER)} END'


# One fragment per filter value, so the list never loads a row it will not show.
# 분석 중 wins over both 분석 대기 and 실패 for a row that is in Codex right now:
# a retry that is being retried is not waiting, and state_of() reads the same order.
STATE_SQL = {
    HANDLED: ('handled = ?', (HANDLED,)),
    PROGRESS: ('handled = ?', (PROGRESS,)),
    '미처리': ("handled = '' AND result IS NOT NULL", ()),
    ANALYZING: ("result IS NULL AND analyzing <> ''", ()),
    '분석 대기': ("result IS NULL AND attempts = 0 AND analyzing = ''", ()),
    FAILED: ("result IS NULL AND attempts > 0 AND analyzing = ''", ()),
}
SORTS = {'received': 'received', 'subject': 'subject', 'sender': 'sender',
         'category': 'category', 'priority': priority_case(), 'state': state_case()}
# The filter values a screen offers, in the order it offers them. '' is 전체.
# 답장 대기 sits next to 미처리 because that is what it narrows.
STATES = ('', '분석 대기', ANALYZING, FAILED, '미처리', WAITING, PROGRESS, HANDLED)


def wait_cutoff(today, days=WAIT_DAYS):
    """`received` 아래로는 WAIT_DAYS일이 지난 것이 되는 경계, UTC ISO 문자열.

    Korean calendar days, through day_bounds(), because '이틀 지났다'는 24시간 x 2가
    아니라 달력 두 장이다 — 화면의 '2일째'와 목록이 거르는 범위가 같은 날짜 계산에서
    나와야 카드와 그 카드가 여는 목록이 다른 메일을 셀 수 없다.
    """
    return day_bounds(today - timedelta(days=days - 1))[0]


def state_where(state, today=None):
    """(WHERE 조각, 파라미터) for one filter value. search()가 쓰는 유일한 통로.

    STATE_SQL holds the fixed ones. 답장 대기 is the one whose boundary moves with the
    calendar, so its parameters are built per call rather than stored beside the
    clause: this process runs for days at a time, and a cutoff frozen at import is a
    filter that quietly means something different tomorrow.
    """
    if state == WAITING:
        return ('reply_needed = 1 AND handled <> ? AND received < ?',
                (HANDLED, wait_cutoff(today or local_now().date())))
    return STATE_SQL.get(state, ('', ()))


def now():
    return datetime.now(timezone.utc).isoformat()


def local_now():
    return datetime.now(KST)


def day_bounds(day):
    """The Korean calendar day as the UTC ISO range that `received` is stored in."""
    start = datetime(day.year, day.month, day.day, tzinfo=KST)
    return start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


class TextHTML(HTMLParser):
    """HTML mail to text, keeping a table's cell boundaries.

    A td that emits nothing joins its neighbours: 수주번호 A26090135 beside 공급가액
    90,000 left Codex — and the 원문 panel — one run reading 'A2609013590,000', with
    no way back to the two figures. Rows are collected instead of flattened, and a
    table that is really a table is emitted as Markdown so the column a cell belongs
    to survives the trip.
    """

    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
        self.tables = []   # one entry per open <table>, innermost last

    def write(self, text):
        """Text goes to the open cell, or to the document when there is none."""
        cell = self.cell()
        (cell if cell is not None else self.parts).append(text)

    def cell(self):
        for table in reversed(self.tables):
            if table and table[-1]:
                return table[-1][-1]
        return None

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag == 'table':
            self.tables.append([])
        elif tag == 'tr' and self.tables:
            self.tables[-1].append([])
        elif tag in ('td', 'th') and self.tables:
            if not self.tables[-1]:
                self.tables[-1].append([])      # a cell outside any <tr>
            self.tables[-1][-1].append([])
        elif tag in ('br', 'p', 'div', 'tr', 'li'):
            self.write('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif tag == 'table' and self.tables:
            # Popped first: a nested table's text belongs to the cell holding it.
            self.write(table_text(self.tables.pop()))

    def handle_data(self, data):
        if not self.hidden:
            self.write(data)

    def text(self):
        """The document, closing any table the mail forgot to."""
        while self.tables:
            self.write(table_text(self.tables.pop()))
        return ''.join(self.parts)


# A layout table is the common case in mail HTML, and a Markdown grid drawn round
# somebody's signature is noise. Only a table of at least this shape gets one.
TABLE_SHAPE = (2, 2)   # columns, rows


# 한글 음절과 자모: the syllables a mail is written in, and the bare letters a
# keyboard leaves behind. A Korean mail is mostly this; an English one has none of it.
HANGUL = ((0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F))


def korean_ratio(text):
    """한글 letters as a share of the letters in `text`, ignoring digits and marks.

    Letters only: a quotation full of part numbers, prices and dates is not a Korean
    mail because the digits outnumber the words, and a Korean one is still Korean
    with an English signature under it.
    """
    letters = [ch for ch in str(text or '') if ch.isalpha()]
    if not letters:
        return 1.0
    korean = sum(1 for ch in letters if any(low <= ord(ch) <= high for low, high in HANGUL))
    return korean / len(letters)


# Under this a mail reads as somebody else's language rather than as a Korean mail
# with a quoted English thread under it, which is the common shape here.
FOREIGN_BELOW = 0.2


def looks_foreign(text):
    """Is this a mail 번역 should be offered for? A judgement, and the screen says so."""
    return korean_ratio(text) < FOREIGN_BELOW


def squash(text):
    """A cell as one line: a Markdown row cannot carry the newlines a cell may hold."""
    return ' '.join(str(text).split()).replace('|', r'\|')


def table_text(table):
    """A collected table as text, Markdown only when it is a grid rather than a layout."""
    rows = [[''.join(cell) for cell in row] for row in table]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ''
    width = max(len(row) for row in rows)
    columns, least = TABLE_SHAPE
    if width < columns or len(rows) < least:
        return '\n' + '\n'.join(layout_rows(rows)) + '\n'
    grid = [[squash(cell) for cell in row] + [''] * (width - len(row)) for row in rows]
    grid.insert(1, ['---'] * width)
    return '\n' + '\n'.join('| ' + ' | '.join(row) + ' |' for row in grid) + '\n'


def layout_rows(rows):
    """A table that is only holding a layout: the cells are text, not columns.

    Most mail HTML wraps the body — or a signature, or a nested real table — in one
    of these, so a cell keeps its own line breaks. Only a row that really has several
    cells is put on one line, where the boundary is the thing worth keeping.
    """
    lines = []
    for row in rows:
        kept = [cell for cell in row if cell.strip()]
        lines.append(' | '.join(squash(cell) for cell in kept) if len(kept) > 1 else kept[0].strip())
    return lines


def text_of_html(html):
    """HTML mail as the text everything downstream reads: Codex, 원문 and the search box."""
    parser = TextHTML()
    parser.feed(str(html))
    return parser.text()


def parse_mail(raw: bytes):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = message.get_body(preferencelist=('plain', 'html'))
    text = ''
    if body:
        text = body.get_content()
        if body.get_content_type() == 'text/html':
            text = text_of_html(text)
    return {
        'sender': str(message.get('From', '')),
        'subject': str(message.get('Subject', NO_SUBJECT)),
        'date': str(message.get('Date', '')),
        'message_id': str(message.get('Message-ID', '')),
        'body': text,
        'attachments': [part.get_filename() or '(이름 없음)' for part in message.iter_attachments()],
    }


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS log (at TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                mail_id TEXT NOT NULL DEFAULT '', role TEXT NOT NULL,
                text TEXT NOT NULL, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_room (
                id TEXT PRIMARY KEY, account TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS todo (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                text TEXT NOT NULL, state TEXT NOT NULL DEFAULT '',
                due TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS note (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                mail_id TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '', pinned INTEGER NOT NULL DEFAULT 0,
                created TEXT NOT NULL, updated TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS event (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                title TEXT NOT NULL, start TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                handled TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS briefing (
                account TEXT NOT NULL, day TEXT NOT NULL, at TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(account,day));
            CREATE TABLE IF NOT EXISTS seen (account TEXT, uid TEXT, PRIMARY KEY(account,uid));
            CREATE TABLE IF NOT EXISTS mail (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, uid TEXT NOT NULL,
                raw BLOB NOT NULL, received TEXT NOT NULL, parsed TEXT,
                result TEXT, exported INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', UNIQUE(account,uid));
        ''')
        self.migrate()

    def migrate(self):
        """Add columns the app needs, leaving the installed database and its rows alone."""
        present = {row['name'] for row in self.db.execute('PRAGMA table_info(mail)')}
        additions = (('handled', "TEXT NOT NULL DEFAULT ''"), ('draft_edit', "TEXT NOT NULL DEFAULT ''"),
                     ('notified', 'INTEGER NOT NULL DEFAULT 0'), ('analyzed_at', "TEXT NOT NULL DEFAULT ''"),
                     ('exported_at', "TEXT NOT NULL DEFAULT ''"), ('subject', "TEXT NOT NULL DEFAULT ''"),
                     ('sender', "TEXT NOT NULL DEFAULT ''"),
                     # Denormalised for the list, exactly as subject/sender are: the
                     # filter and the sort must not have to parse every result JSON.
                     ('category', "TEXT NOT NULL DEFAULT ''"),
                     ('priority', "TEXT NOT NULL DEFAULT ''"),
                     # The worker's own marker, and the one card the kanban was told
                     # to forget. Both belong to a mail, so both are columns on it.
                     ('analyzing', "TEXT NOT NULL DEFAULT ''"),
                     ('todo_hidden', 'INTEGER NOT NULL DEFAULT 0'),
                     # 한글 번역, kept beside the mail it belongs to: it is asked for
                     # once, read many times, and costs a Codex run to make again.
                     ('translated', "TEXT NOT NULL DEFAULT ''"),
                     ('translated_from', "TEXT NOT NULL DEFAULT ''"),
                     # 마지막으로 실패한 시각. 포기해도 되는 실패인지를 이것으로 가른다 —
                     # 그 뒤에 다른 메일이 분석에 성공했다면 Codex는 멀쩡하고 이 메일이
                     # 문제라는 뜻이고, 아무것도 성공하지 못했다면 한도나 로그인 쪽이다.
                     ('failed_at', "TEXT NOT NULL DEFAULT ''"),
                     # 답장이 필요한가, 분석의 답 그대로. 세 값을 가진다: 1 예, 0 아니오,
                     # -1 아직 모름. 기본값이 0이 아니라 -1인 것이 backfill_replies()가
                     # 끝을 아는 유일한 방법이다 — 0은 '답장 필요 없음'이라는 진짜 답이라
                     # 아직 채우지 않은 행과 구별되지 않는다.
                     ('reply_needed', 'INTEGER NOT NULL DEFAULT -1'))
        with self.db:
            for column, declaration in additions:
                if column not in present:
                    self.db.execute(f'ALTER TABLE mail ADD COLUMN {column} {declaration}')
        self.backfill_headers()
        self.backfill_verdicts()
        self.backfill_replies()

    def backfill_headers(self):
        """Fill subject/sender for mail collected before those columns existed."""
        rows = self.db.execute("SELECT id, parsed, raw FROM mail WHERE subject=''").fetchall()
        updates = []
        for row in rows:
            try:
                parsed = json.loads(row['parsed']) if row['parsed'] else parse_mail(row['raw'])
            except Exception:
                continue
            updates.append((parsed.get('subject', ''), parsed.get('sender', ''), row['id']))
        if updates:
            with self.db:
                self.db.executemany('UPDATE mail SET subject=?, sender=? WHERE id=?', updates)

    def backfill_verdicts(self):
        """Fill category/priority for mail analysed before those columns existed."""
        rows = self.db.execute("SELECT id, result FROM mail "
                               "WHERE result IS NOT NULL AND category='' AND priority=''").fetchall()
        updates = []
        for row in rows:
            try:
                result = json.loads(row['result'])
            except ValueError:
                continue
            updates.append((result.get('category', ''), result.get('priority', ''), row['id']))
        if updates:
            with self.db:
                self.db.executemany('UPDATE mail SET category=?, priority=? WHERE id=?', updates)

    def backfill_replies(self):
        """Fill reply_needed for mail analysed before the column existed.

        `= -1` is an exact match for 'never filled', so this converges and then costs
        one indexless count of nothing — unlike the two above, whose '' test a real
        answer could also satisfy.
        """
        rows = self.db.execute('SELECT id, result FROM mail '
                               'WHERE result IS NOT NULL AND reply_needed = -1').fetchall()
        updates = []
        for row in rows:
            try:
                result = json.loads(row['result'])
            except ValueError:
                # 읽을 수 없는 결과는 0으로 내려 둔다. -1로 두면 이 쿼리가 매번 같은
                # 행을 다시 집어 든다.
                updates.append((0, row['id']))
                continue
            updates.append((1 if result.get('reply_needed') else 0, row['id']))
        if updates:
            with self.db:
                self.db.executemany('UPDATE mail SET reply_needed=? WHERE id=?', updates)

    def get_meta(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, value))

    BRIEF_KEEP = 30

    def save_briefing(self, account, day, result):
        """One row per day, replaced when the reader asks for it again."""
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO briefing VALUES (?,?,?,?)',
                            (account, day, now(), json.dumps(result, ensure_ascii=False)))
            self.db.execute('DELETE FROM briefing WHERE account=? AND day NOT IN '
                            '(SELECT day FROM briefing WHERE account=? ORDER BY day DESC LIMIT ?)',
                            (account, account, self.BRIEF_KEEP))

    def briefing(self, account):
        """The newest briefing, whatever day it is for — the screen says which."""
        return self.db.execute('SELECT * FROM briefing WHERE account=? ORDER BY day DESC LIMIT 1',
                               (account,)).fetchone()

    def seen(self, account):
        return {r[0] for r in self.db.execute('SELECT uid FROM seen WHERE account=?', (account,))}

    def baseline(self, account, uids):
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO seen VALUES (?,?)', ((account, uid) for uid in uids))
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('baseline:' + account, now()))

    def add(self, account, uid, raw):
        ident = hashlib.sha256((account + '\0' + uid).encode()).hexdigest()[:24]
        try:
            headers = parse_mail(raw)
        except Exception:
            # A malformed mail must still be stored; the subject can be filled in later.
            headers = {'subject': '', 'sender': ''}
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO mail(id,account,uid,raw,received,subject,sender) '
                            'VALUES (?,?,?,?,?,?,?)',
                            (ident, account, uid, raw, now(), headers.get('subject', ''), headers.get('sender', '')))
            self.db.execute('INSERT OR IGNORE INTO seen VALUES (?,?)', (account, uid))
        return ident

    def pending(self, account, timestamp):
        """분석을 기다리는 메일. 묶을지 한 통씩 갈지는 worker가 group_mails()로 정한다.

        LIMIT is one poll's own collection cap: the worker sends these in batches now,
        so a cycle that used to cost five Codex calls costs one or two. rowid breaks the
        tie for the same reason page() needs it — a cycle that stored several mails gave
        them all one `received`, and which of them Codex reads first would otherwise be
        whatever sqlite felt like, differently on each call.
        """
        return self.db.execute('SELECT * FROM mail WHERE account=? AND result IS NULL AND retry_at<=? ORDER BY received, rowid LIMIT 25',
                               (account, timestamp)).fetchall()

    def analyzed(self, ident, parsed, result):
        with self.db:
            self.db.execute('UPDATE mail SET parsed=?, result=?, error=\'\', analyzed_at=?, '
                            'category=?, priority=?, reply_needed=? WHERE id=?',
                            (json.dumps(parsed, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
                             now(), result.get('category', ''), result.get('priority', ''),
                             1 if result.get('reply_needed') else 0, ident))

    def failed(self, ident, error, timestamp):
        with self.db:
            self.db.execute('UPDATE mail SET attempts=attempts+1, error=?, retry_at=?, '
                            'failed_at=? WHERE id=?', (error, timestamp, now(), ident))

    def analyzed_since(self, account, stamp):
        """이 시각 뒤에 분석에 성공한 메일이 있는가. 포기해도 되는 실패인지를 이것이 가른다.

        Without it, an account that has run out of quota fails every mail in turn and
        five hours later the whole queue is marked 포기 — the one failure this cap must
        not be able to cause. An empty stamp is a mail that has never failed.
        """
        if not stamp:
            return False
        return self.db.execute('SELECT 1 FROM mail WHERE account=? AND analyzed_at>? LIMIT 1',
                               (account, stamp)).fetchone() is not None

    def unexported(self, account):
        return self.db.execute('SELECT * FROM mail WHERE account=? AND result IS NOT NULL AND exported=0 ORDER BY received LIMIT 50', (account,)).fetchall()

    def exported(self, ids):
        stamp = now()
        with self.db:
            self.db.executemany('UPDATE mail SET exported=1, exported_at=? WHERE id=?',
                                ((stamp, i) for i in ids))

    def counts(self, account):
        return dict(self.db.execute('SELECT COUNT(*) total, COALESCE(SUM(result IS NULL),0) pending, COALESCE(SUM(result IS NOT NULL AND exported=0),0) waiting FROM mail WHERE account=?', (account,)).fetchone())

    # category/priority/reply_needed are the denormalised verdict: the list already
    # filters and sorts on them, and a screen that reads them here rather than out of
    # the result JSON cannot disagree with the query that found the row.
    LIST_COLUMNS = ('id, received, subject, sender, parsed, result, handled, draft_edit, '
                    'attempts, retry_at, error, exported, notified, analyzing, todo_hidden, '
                    'category, priority, reply_needed')

    def page(self, account, limit=2000):
        """Newest first, without the raw blob. Filtering happens in filter_rows().

        rowid breaks the tie. `received` is written by now() at insert, and a Windows
        clock ticks about every 15ms, so a cycle that stores several mails gives them
        the same string — and an ORDER BY with no tie-break may then hand back a
        different order every call. rowid is the order they arrived in.
        """
        return self.db.execute(f'SELECT {self.LIST_COLUMNS} FROM mail WHERE account=? '
                               'ORDER BY received DESC, rowid DESC LIMIT ?',
                               (account, limit)).fetchall()

    def search(self, account, query='', state='', sort='received', desc=True,
               limit=LIST_LIMIT, offset=0, category='', priority=''):
        """(page of rows, total). The list used to read 2000 rows and sort them in Python.

        `category` and `priority` are the columns the 대시보드 bars are drawn from, so a
        bar and the list it opens count the same mail: both are written by analyzed()
        out of one result, and neither is read back out of the JSON.
        """
        where, params = ['account = ?'], [account]
        text = query.strip()
        if text:
            like = f'%{text}%'
            # parsed carries the body and result the summary and requests, so the box
            # finds what the user remembers reading, not just what the list shows.
            where.append('(subject LIKE ? OR sender LIKE ? OR parsed LIKE ? OR result LIKE ?)')
            params += [like] * 4
        clause, extra = state_where(state)
        if clause:
            where.append(f'({clause})')
            params += list(extra)
        # The column name is ours; the value is always a parameter.
        for column, chosen in (('category', category), ('priority', priority)):
            if chosen:
                where.append(f'{column} = ?')
                params.append(chosen)
        condition = ' AND '.join(where)
        total = self.db.execute(f'SELECT COUNT(*) FROM mail WHERE {condition}',
                                params).fetchone()[0]
        order = SORTS.get(sort) or SORTS['received']
        rows = self.db.execute(
            f'SELECT {self.LIST_COLUMNS} FROM mail WHERE {condition} '
            f"ORDER BY {order} {'DESC' if desc else 'ASC'}, received DESC, rowid DESC "
            'LIMIT ? OFFSET ?',
            params + [limit, offset]).fetchall()
        return rows, total

    def add_todo(self, account, text, due=''):
        with self.db:
            cursor = self.db.execute('INSERT INTO todo(account,text,due,created) VALUES (?,?,?,?)',
                                     (account, text, due, now()))
        return cursor.lastrowid

    def todos(self, account):
        return self.db.execute('SELECT id, text, state, due, created FROM todo '
                               'WHERE account=? ORDER BY id', (account,)).fetchall()

    def set_todo_state(self, ident, state):
        with self.db:
            self.db.execute('UPDATE todo SET state=? WHERE id=?', (state, ident))

    def delete_todo(self, ident):
        with self.db:
            self.db.execute('DELETE FROM todo WHERE id=?', (ident,))

    # 메모. What is neither a mail, a dated event nor a task: a phone number, where a
    # template lives, a line caught in a meeting. `mail_id` is the same key chat uses —
    # a mail's id, or '' for a free-standing note — and it is the whole reason this is
    # not a second 할 일 판: a memo can belong to the mail it was written about.

    def add_note(self, account, text='', color='', mail_id=''):
        stamp = now()
        with self.db:
            cursor = self.db.execute(
                'INSERT INTO note(account,mail_id,text,color,created,updated) '
                'VALUES (?,?,?,?,?,?)', (account, mail_id, text, color, stamp, stamp))
        return cursor.lastrowid

    def notes(self, account, mail_id=None):
        """Pinned first, then most recently written. `mail_id` narrows to one mail's.

        `id DESC` is the last tie-break for the reason every mail list ends with rowid:
        `now()` is a string off a Windows clock that ticks about every 15ms, so notes
        written in one burst share a stamp and the order would otherwise be sqlite's
        to choose again on every call.
        """
        where, params = ['account=?'], [account]
        if mail_id is not None:
            where.append('mail_id=?')
            params.append(mail_id)
        return self.db.execute(
            'SELECT id, mail_id, text, color, pinned, created, updated FROM note '
            f"WHERE {' AND '.join(where)} ORDER BY pinned DESC, updated DESC, id DESC",
            params).fetchall()

    def set_note_text(self, ident, text):
        """Only the text moves `updated`: the wall is ordered by when it was written."""
        with self.db:
            self.db.execute('UPDATE note SET text=?, updated=? WHERE id=?',
                            (text, now(), ident))

    def set_note_color(self, ident, color):
        with self.db:
            self.db.execute('UPDATE note SET color=? WHERE id=?', (color, ident))

    def set_note_pinned(self, ident, pinned=True):
        with self.db:
            self.db.execute('UPDATE note SET pinned=? WHERE id=?',
                            (1 if pinned else 0, ident))

    def delete_note(self, ident):
        with self.db:
            self.db.execute('DELETE FROM note WHERE id=?', (ident,))

    def delete_empty_notes(self, account, keep=None):
        """Sweep memos nothing was ever written in.

        '새 메모' inserts the row so the card can appear and take the caret at once,
        which means a blank one is what 'clicked it and changed my mind' leaves
        behind. It is swept the next time the wall is rebuilt for some other reason,
        never while it is on screen — there is nothing in it to lose.
        """
        # Two-argument trim: the one-argument form strips spaces only, and a memo
        # opened and left alone holds whatever newline the caret put there.
        with self.db:
            self.db.execute(
                "DELETE FROM note WHERE account=? AND id IS NOT ? "
                "AND trim(text, ' ' || char(9) || char(10) || char(13))=''",
                (account, keep))

    def detach_notes(self, ids):
        """Cut memos loose from mail that is being deleted, rather than deleting them.

        The analysis, the draft and the chat all came from the mail and go with it; a
        memo is the user's own writing about it, and throwing that away because they
        threw the mail away is the same edit-eating the draft box is guarded against.
        The note survives as a free-standing one, which is what `mail_id=''` means.
        """
        with self.db:
            self.db.executemany("UPDATE note SET mail_id='' WHERE mail_id=?",
                                ((i,) for i in ids))

    # 직접 추가한 일정. Analysis produces the rest, and a mailbox that never mentions
    # a date cannot be made to: this is the one way a person puts one on the calendar.
    # It is a table rather than a column because it belongs to no mail.

    def add_event(self, account, title, start='', deadline='', note=''):
        with self.db:
            cursor = self.db.execute(
                'INSERT INTO event(account,title,start,deadline,note,created) '
                'VALUES (?,?,?,?,?,?)', (account, title, start, deadline, note, now()))
        return cursor.lastrowid

    def events(self, account):
        return self.db.execute(
            'SELECT id, title, start, deadline, note, handled, created FROM event '
            'WHERE account=? ORDER BY id', (account,)).fetchall()

    def set_event_handled(self, ident, state):
        """The tick on the 마감 checklist, for a row that owns no mail to mark."""
        with self.db:
            self.db.execute('UPDATE event SET handled=? WHERE id=?', (state, ident))

    def delete_event(self, ident):
        with self.db:
            self.db.execute('DELETE FROM event WHERE id=?', (ident,))

    def unedited_drafts(self, account):
        """Analysed, unhandled, not edited by a person. overview.review_queue() then
        decides which of these actually asked for a reply — matching on the JSON text
        would depend on how json.dumps happened to space its separators."""
        return self.db.execute(f'SELECT {self.LIST_COLUMNS} FROM mail WHERE account=? '
                               "AND result IS NOT NULL AND draft_edit='' AND handled<>? "
                               'ORDER BY received DESC, rowid DESC',
                               (account, HANDLED)).fetchall()

    def add_chat(self, account, role, text, mail_id=''):
        with self.db:
            self.db.execute('INSERT INTO chat(account,mail_id,role,text,at) VALUES (?,?,?,?,?)',
                            (account, mail_id, role, text, now()))

    def chat(self, account, mail_id='', limit=40):
        """Oldest first. One thread per mail, plus a general one when mail_id is ''."""
        rows = self.db.execute('SELECT role, text, at FROM chat WHERE account=? AND mail_id=? '
                               'ORDER BY id DESC LIMIT ?', (account, mail_id, limit)).fetchall()
        return list(reversed(rows))

    def clear_chat(self, account, mail_id=''):
        with self.db:
            self.db.execute('DELETE FROM chat WHERE account=? AND mail_id=?', (account, mail_id))

    # --- 상담 rooms -------------------------------------------------------
    #
    # `chat.mail_id` was always the room key: a mail's id, or '' for the general
    # thread. A free-standing room is the third kind and needs somewhere to keep a
    # title, which is all `chat_room` is. Its ids carry ROOM_MARK, which a mail id
    # cannot: those are 24 hex characters.

    def new_room(self, account, title=''):
        ident = ROOM_MARK + secrets.token_hex(8)
        with self.db:
            self.db.execute('INSERT INTO chat_room(id,account,title,created) VALUES (?,?,?,?)',
                            (ident, account, title, now()))
        return ident

    def name_room(self, ident, title, only_if_unnamed=False):
        """Rename a room, or let it name itself after its first question exactly once.

        The guard is in the UPDATE rather than in a read-then-write: a second question
        sent while the first was still being answered would otherwise rename the room
        out from under the title it had just taken.
        """
        clause = " AND title=''" if only_if_unnamed else ''
        with self.db:
            self.db.execute(f'UPDATE chat_room SET title=? WHERE id=?{clause}', (title, ident))

    def drop_room(self, account, ident):
        """A free room and its turns. A mail's thread goes with the mail, not through here."""
        if not str(ident).startswith(ROOM_MARK):
            return False
        with self.db:
            self.db.execute('DELETE FROM chat WHERE account=? AND mail_id=?', (account, ident))
            self.db.execute('DELETE FROM chat_room WHERE id=? AND account=?', (ident, account))
        return True

    def rooms(self, account):
        """Every 상담 thread that exists, newest activity first.

        The general thread is always in the list even when it is empty, because it is
        where the page lands when nothing else is asked for. A room with no turns yet
        sorts by when it was made, so 새 대화 appears at the top where it was created.
        """
        turns = self.db.execute(
            'SELECT mail_id AS key, COUNT(*) AS turns, MAX(at) AS at,'
            ' (SELECT role FROM chat WHERE account=c.account AND mail_id=c.mail_id'
            '  ORDER BY id DESC LIMIT 1) AS role,'
            ' (SELECT text FROM chat WHERE account=c.account AND mail_id=c.mail_id'
            '  ORDER BY id DESC LIMIT 1) AS last'
            ' FROM chat c WHERE c.account=? GROUP BY mail_id', (account,)).fetchall()
        found = {row['key']: dict(row) for row in turns}
        for row in self.db.execute('SELECT id, title, created FROM chat_room WHERE account=?',
                                   (account,)).fetchall():
            entry = found.setdefault(row['id'], {'key': row['id'], 'turns': 0, 'at': row['created'],
                                                 'role': '', 'last': ''})
            entry['name'] = row['title']
        found.setdefault('', {'key': '', 'turns': 0, 'at': '', 'role': '', 'last': ''})
        # Subjects for the mail threads, in one query rather than one per row.
        wanted = [key for key in found if key and not key.startswith(ROOM_MARK)]
        for ident, subject in self.mail_subjects(wanted).items():
            found[ident]['name'] = subject
        return sorted(found.values(), key=lambda row: row['at'], reverse=True)

    def mail_subjects(self, ids):
        """{id: subject}, in one IN (…) rather than one query per row.

        Both 상담 and 메모 hang their own rows off a mail id and both need the subject
        to name it on screen; one query shape means the two cannot answer differently
        for a mail that has since been deleted — it is simply absent from the map.
        """
        wanted = [ident for ident in dict.fromkeys(ids) if ident]
        if not wanted:
            return {}
        marks = ','.join('?' * len(wanted))
        return {row['id']: row['subject'] for row in self.db.execute(
            f'SELECT id, subject FROM mail WHERE id IN ({marks})', wanted).fetchall()}

    def mail_cards(self, ids):
        """The list columns of several mail at once, for the places that only hover.

        mail_subjects() answers 'what is this called'; the 브리핑's 먼저 볼 메일 has to
        say who sent it and how urgent it is without opening it, and a mail deleted
        since the briefing was written is simply absent — which is what lets the pin
        be left undrawn rather than opening an empty 메일 화면.
        """
        wanted = [ident for ident in dict.fromkeys(ids) if ident]
        if not wanted:
            return {}
        marks = ','.join('?' * len(wanted))
        return {row['id']: row for row in self.db.execute(
            f'SELECT {self.LIST_COLUMNS} FROM mail WHERE id IN ({marks})', wanted).fetchall()}

    def set_handled_many(self, ids, state):
        with self.db:
            self.db.executemany('UPDATE mail SET handled=? WHERE id=?',
                                ((state, ident) for ident in ids))

    def detail(self, ident):
        return self.db.execute('SELECT * FROM mail WHERE id=?', (ident,)).fetchone()

    def delete(self, ids):
        """Remove mail for good, with whatever chat hung off it.

        `seen` keeps the uid on purpose: the mail is still on the POP3 server, and
        forgetting it would collect and analyse the very mail the user just threw away
        on the next poll. Rows already written to the workbook stay there — this
        database is not what Excel reads. Memos are cut loose rather than dropped —
        see detach_notes().
        """
        self.detach_notes(ids)
        with self.db:
            self.db.executemany('DELETE FROM mail WHERE id=?', ((i,) for i in ids))
            self.db.executemany('DELETE FROM chat WHERE mail_id=?', ((i,) for i in ids))

    def set_handled(self, ident, state):
        with self.db:
            self.db.execute('UPDATE mail SET handled=? WHERE id=?', (state, ident))

    def set_draft(self, ident, text):
        with self.db:
            self.db.execute('UPDATE mail SET draft_edit=? WHERE id=?', (text, ident))

    def set_reply_draft(self, ident, subject, text):
        """초안 만들기's answer, into the analysis it belongs to.

        Into `reply_draft` and not `draft_edit`, because that is what this is: the
        analysis's own draft, which `review_queue()` reads as 'nobody has been here
        yet'. `reply_needed` goes true with it — asking for a draft is the answer to
        that question, however the analysis had judged it.

        `draft_edit` is cleared, and that is the one place in this app where text a
        person typed is thrown away. It is what the press asked for: the screen shows
        `draft_edit or reply_draft`, so leaving an edit in place would store a draft
        nobody could see, and the button says 초안 새로 만들기 when there is something
        to replace. Nothing here runs on a timer.
        """
        row = self.db.execute('SELECT result FROM mail WHERE id=?', (ident,)).fetchone()
        if row is None or not row['result']:
            return False
        result = json.loads(row['result'])
        result['reply_draft'] = text or ''
        result['reply_needed'] = True
        if subject:
            result['reply_subject'] = subject
        with self.db:
            self.db.execute("UPDATE mail SET result=?, draft_edit='' WHERE id=?",
                            (json.dumps(result, ensure_ascii=False), ident))
        return True

    def set_translation(self, ident, language, text):
        """The Korean of a foreign mail, and the language it was written in."""
        with self.db:
            self.db.execute('UPDATE mail SET translated=?, translated_from=? WHERE id=?',
                            (text or '', language or '', ident))

    def reset(self, ids, reanalyze=False):
        """Clear the backoff so the next cycle picks these up again; count what moved.

        A mail Codex is looking at right now is skipped: clearing its result would be
        overwritten by the answer already on its way, so the request would vanish with
        nothing on screen saying so. The count is what lets the screen say it instead.
        """
        # reply_needed goes back to -1 with the rest of the verdict: it *is* part of
        # the answer being thrown away, and left at 1 the mail would sit in 답장 대기
        # describing an analysis that no longer exists.
        clause = (", result=NULL, analyzed_at='', category='', priority='', reply_needed=-1"
                  if reanalyze else '')
        changed = 0
        with self.db:
            for ident in ids:
                cursor = self.db.execute(
                    f"UPDATE mail SET attempts=0, retry_at=0, error='', failed_at=''{clause} "
                    "WHERE id=? AND analyzing=''", (ident,))
                changed += cursor.rowcount
        return changed

    def analyzing(self, account):
        """The first id Codex is on right now, or '' — a batch marks all of its own."""
        row = self.db.execute("SELECT id FROM mail WHERE account=? AND analyzing<>'' "
                              'ORDER BY rowid LIMIT 1', (account,)).fetchone()
        return row[0] if row else ''

    def mark_analyzing(self, account, ident):
        """Only what Codex has right now carries the marker; clearing first keeps it true.

        An id or a list of them: one codex exec now reads up to BATCH_MAILS at a time,
        and every one of those rows is 분석 중 until that call comes back.
        """
        ids = [ident] if isinstance(ident, str) else [one for one in ident if one]
        with self.db:
            self.db.execute("UPDATE mail SET analyzing='' WHERE account=? AND analyzing<>''",
                            (account,))
            stamp = now()
            for one in ids:
                if one:
                    self.db.execute('UPDATE mail SET analyzing=? WHERE id=?', (stamp, one))

    def set_todo_hidden(self, ident, hidden=True):
        """Take a mail's card off the 할 일 판 without touching the mail itself."""
        with self.db:
            self.db.execute('UPDATE mail SET todo_hidden=? WHERE id=?',
                            (1 if hidden else 0, ident))

    def retryable(self, account):
        return self.db.execute(f'SELECT {self.LIST_COLUMNS} FROM mail '
                               'WHERE account=? AND result IS NULL ORDER BY received', (account,)).fetchall()

    def day_counts(self, account, start, end):
        window = 'AND {0}>=? AND {0}<?'
        query = ('SELECT (SELECT COUNT(*) FROM mail WHERE account=? ' + window.format('received') + ') collected, '
                 '(SELECT COUNT(*) FROM mail WHERE account=? ' + window.format('analyzed_at') + ') analyzed, '
                 '(SELECT COUNT(*) FROM mail WHERE account=? ' + window.format('exported_at') + ') exported')
        return dict(self.db.execute(query, (account, start, end) * 3).fetchone())

    def open_count(self, account):
        return self.db.execute('SELECT COUNT(*) FROM mail WHERE account=? AND result IS NOT NULL AND handled<>?',
                               (account, HANDLED)).fetchone()[0]

    def unnotified(self, account):
        return self.db.execute(f'SELECT {self.LIST_COLUMNS} FROM mail WHERE account=? '
                               'AND result IS NOT NULL AND notified=0 ORDER BY received', (account,)).fetchall()

    def mark_notified(self, ids):
        with self.db:
            self.db.executemany('UPDATE mail SET notified=1 WHERE id=?', ((i,) for i in ids))


class LogStore:
    """The log table on a connection of its own, so any thread can hold one.

    Store owns the mail tables and runs migrate() on every open; the run log needs
    neither, and a sqlite connection cannot be shared across threads.
    """

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute(LOG_SCHEMA)
        self.db.commit()
        self.writes = 0

    def add(self, text, keep=0):
        with self.db:
            self.db.execute('INSERT INTO log(at,text) VALUES (?,?)', (now(), text))
        self.writes += 1
        if keep and self.writes % LOG_TRIM_EVERY == 0:
            self.trim(keep)

    def trim(self, keep):
        """Bounded on purpose: this file is also the only copy of the collected mail."""
        with self.db:
            self.db.execute('DELETE FROM log WHERE rowid <= (SELECT MAX(rowid) - ? FROM log)',
                            (keep,))

    def recent(self, limit):
        """Newest last, so a screen that just opened can print it top to bottom."""
        rows = self.db.execute('SELECT at, text FROM log ORDER BY rowid DESC LIMIT ?',
                               (limit,)).fetchall()
        return [(row[0], row[1]) for row in reversed(rows)]

    def close(self):
        self.db.close()


def local_text(stamp, pattern='%m-%d %H:%M'):
    """UTC ISO string -> Korean local time for display."""
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(KST).strftime(pattern)
    except (TypeError, ValueError):
        return str(stamp or '')


def state_of(row):
    if row['handled'] == HANDLED:
        return HANDLED
    if row['handled'] == PROGRESS:
        return PROGRESS
    if row['result']:
        return '미처리'
    # Before 실패 on purpose: a mail on its third attempt, in Codex right now, is
    # being analysed — saying '2회 실패' of it is a week-old fact.
    if row['analyzing']:
        return ANALYZING
    if row['attempts']:
        return f"{row['attempts']}회 실패"
    return '분석 대기'


def row_view(row):
    """One line of the mail list. Works before analysis, when result is still empty."""
    result = json.loads(row['result']) if row['result'] else {}
    # 답장 대기 is not a state_of() answer — it narrows 미처리 by the calendar — so it
    # rides beside `state` rather than in it, and filter_rows() reads it there. Without
    # it the fallback window offers the filter in its dropdown and then lists nothing.
    days = waiting_days(row, local_now().date())
    return {'id': row['id'], 'received': local_text(row['received']),
            'sender': row['sender'], 'subject': row['subject'] or '(제목 없음)',
            'category': result.get('category', ''), 'priority': result.get('priority', ''),
            'state': state_of(row), 'error': row['error'],
            'waiting': days is not None and days >= WAIT_DAYS}


def waiting_days(row, today):
    """답장 대기의 경과일, 아니면 None. 세 화면이 같은 계산을 쓰게 하는 한 자리.

    The web card, the 대시보드 panel and the tkinter list all ask this question, and
    the SQL in state_where() asks it a fourth way — of the same `reply_needed` column
    and the same calendar-day cutoff, which is what keeps the four answers one answer.
    """
    if row['reply_needed'] != 1 or row['handled'] == HANDLED:
        return None
    try:
        day = date.fromisoformat(local_text(row['received'], '%Y-%m-%d'))
    except ValueError:
        return None
    # max(0, …): `received` is the collection time, and a PC whose clock is behind the
    # mail server's produces a mail from tomorrow. '-3일째' is not a thing.
    return max(0, (today - day).days)


def matches_state(state, wanted):
    """'실패' has to match every retry count, or failed mail is unreachable from the filter."""
    if not wanted:
        return True
    if wanted == FAILED:
        return state.endswith(FAILED)
    return state == wanted


def filter_rows(views, query='', state=''):
    text = query.strip().lower()
    return [view for view in views
            if (not text or text in view['subject'].lower() or text in view['sender'].lower())
            and (view['waiting'] if state == WAITING else matches_state(view['state'], state))]


def account_key(config):
    return config['host'].lower() + ':' + str(config['port']) + '/' + config['email'].lower()


HEADERS = {
    '메일 목록': ['메일 ID', '수신 확인 시각', '발신자', '제목', '종류', '요약', '요청사항', '첨부파일', '처리 상태'],
    '일정': ['항목 ID', '메일 ID', '일정명', '시작', '마감', '근거 문구', '확인 필요', '처리 상태'],
    '우선순위': ['메일 ID', '우선순위', '판단 근거', '다음 행동', '처리 상태'],
    '답변 초안': ['메일 ID', '수신자', '답변 제목', '생성 초안', '사용자 수정본', '검토 상태'],
    '실행 상태': ['항목', '값'],
}


def workbook_rows(row):
    parsed, result = json.loads(row['parsed']), json.loads(row['result'])
    ident = row['id']
    return {
        '메일 목록': [[ident, row['received'], parsed['sender'], parsed['subject'], result['category'], result['summary'], result['requests'], ', '.join(parsed['attachments']), '미처리']],
        '일정': [[f'{ident}:{i}', ident, e['title'], e['start'], e['deadline'], e['evidence'], '필요' if e['needs_review'] else '', '미처리'] for i, e in enumerate(result['events'])],
        '우선순위': [[ident, result['priority'], result['priority_reason'], result['next_action'], '미처리']],
        '답변 초안': [[ident, parsed['sender'], result['reply_subject'], result['reply_draft'], '', '검토 전']] if result['reply_needed'] else [],
    }
