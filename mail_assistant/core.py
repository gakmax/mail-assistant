from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path


KST = timezone(timedelta(hours=9))
HANDLED = '처리'
PROGRESS = '진행'      # the kanban's middle column; '' -> 진행 -> 처리
FAILED = '실패'
LOG_SCHEMA = 'CREATE TABLE IF NOT EXISTS log (at TEXT NOT NULL, text TEXT NOT NULL)'
LOG_TRIM_EVERY = 50
LIST_LIMIT = 50
# Kept here rather than imported from dashboard, which imports this module. A test
# asserts the two agree.
PRIORITY_ORDER = ('긴급', '높음', '보통', '낮음')


def sql_text(value):
    """A literal for our own constants only. User input always goes through a parameter."""
    if "'" in value:
        raise ValueError(f'SQL 리터럴에 쓸 수 없는 값입니다: {value!r}')
    return "'" + value + "'"


def state_case():
    """state_of() as SQL, so '상태' can be sorted without reading every row."""
    return (f'CASE WHEN handled = {sql_text(HANDLED)} THEN 4 '
            f'WHEN handled = {sql_text(PROGRESS)} THEN 3 '
            'WHEN result IS NOT NULL THEN 2 WHEN attempts > 0 THEN 1 ELSE 0 END')


def priority_case():
    """긴급 first. Alphabetical order means nothing for these four words."""
    whens = ' '.join(f'WHEN {sql_text(name)} THEN {rank}'
                     for rank, name in enumerate(PRIORITY_ORDER))
    return f'CASE priority {whens} ELSE {len(PRIORITY_ORDER)} END'


# One fragment per filter value, so the list never loads a row it will not show.
STATE_SQL = {
    HANDLED: ('handled = ?', (HANDLED,)),
    PROGRESS: ('handled = ?', (PROGRESS,)),
    '미처리': ("handled = '' AND result IS NOT NULL", ()),
    '분석 대기': ('result IS NULL AND attempts = 0', ()),
    FAILED: ('result IS NULL AND attempts > 0', ()),
}
SORTS = {'received': 'received', 'subject': 'subject', 'sender': 'sender',
         'category': 'category', 'priority': priority_case(), 'state': state_case()}
# The filter values a screen offers, in the order it offers them. '' is 전체.
STATES = ('', '분석 대기', FAILED, '미처리', PROGRESS, HANDLED)


def now():
    return datetime.now(timezone.utc).isoformat()


def local_now():
    return datetime.now(KST)


def day_bounds(day):
    """The Korean calendar day as the UTC ISO range that `received` is stored in."""
    start = datetime(day.year, day.month, day.day, tzinfo=KST)
    return start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'tr', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_mail(raw: bytes):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = message.get_body(preferencelist=('plain', 'html'))
    text = ''
    if body:
        text = body.get_content()
        if body.get_content_type() == 'text/html':
            parser = TextHTML()
            parser.feed(text)
            text = ''.join(parser.parts)
    return {
        'sender': str(message.get('From', '')),
        'subject': str(message.get('Subject', '(제목 없음)')),
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
            CREATE TABLE IF NOT EXISTS todo (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                text TEXT NOT NULL, state TEXT NOT NULL DEFAULT '',
                due TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
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
                     ('priority', "TEXT NOT NULL DEFAULT ''"))
        with self.db:
            for column, declaration in additions:
                if column not in present:
                    self.db.execute(f'ALTER TABLE mail ADD COLUMN {column} {declaration}')
        self.backfill_headers()
        self.backfill_verdicts()

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

    def get_meta(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, value))

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
        return self.db.execute('SELECT * FROM mail WHERE account=? AND result IS NULL AND retry_at<=? ORDER BY received LIMIT 5',
                               (account, timestamp)).fetchall()

    def analyzed(self, ident, parsed, result):
        with self.db:
            self.db.execute('UPDATE mail SET parsed=?, result=?, error=\'\', analyzed_at=?, '
                            'category=?, priority=? WHERE id=?',
                            (json.dumps(parsed, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
                             now(), result.get('category', ''), result.get('priority', ''), ident))

    def failed(self, ident, error, timestamp):
        with self.db:
            self.db.execute('UPDATE mail SET attempts=attempts+1, error=?, retry_at=? WHERE id=?',
                            (error, timestamp, ident))

    def unexported(self, account):
        return self.db.execute('SELECT * FROM mail WHERE account=? AND result IS NOT NULL AND exported=0 ORDER BY received LIMIT 50', (account,)).fetchall()

    def exported(self, ids):
        stamp = now()
        with self.db:
            self.db.executemany('UPDATE mail SET exported=1, exported_at=? WHERE id=?',
                                ((stamp, i) for i in ids))

    def counts(self, account):
        return dict(self.db.execute('SELECT COUNT(*) total, COALESCE(SUM(result IS NULL),0) pending, COALESCE(SUM(result IS NOT NULL AND exported=0),0) waiting FROM mail WHERE account=?', (account,)).fetchone())

    LIST_COLUMNS = ('id, received, subject, sender, parsed, result, handled, draft_edit, '
                    'attempts, retry_at, error, exported, notified')

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
               limit=LIST_LIMIT, offset=0):
        """(page of rows, total). The list used to read 2000 rows and sort them in Python."""
        where, params = ['account = ?'], [account]
        text = query.strip()
        if text:
            like = f'%{text}%'
            # parsed carries the body and result the summary and requests, so the box
            # finds what the user remembers reading, not just what the list shows.
            where.append('(subject LIKE ? OR sender LIKE ? OR parsed LIKE ? OR result LIKE ?)')
            params += [like] * 4
        clause, extra = STATE_SQL.get(state, ('', ()))
        if clause:
            where.append(f'({clause})')
            params += list(extra)
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
        database is not what Excel reads.
        """
        with self.db:
            self.db.executemany('DELETE FROM mail WHERE id=?', ((i,) for i in ids))
            self.db.executemany('DELETE FROM chat WHERE mail_id=?', ((i,) for i in ids))

    def set_handled(self, ident, state):
        with self.db:
            self.db.execute('UPDATE mail SET handled=? WHERE id=?', (state, ident))

    def set_draft(self, ident, text):
        with self.db:
            self.db.execute('UPDATE mail SET draft_edit=? WHERE id=?', (text, ident))

    def reset(self, ids, reanalyze=False):
        """Clear the backoff so the next cycle picks these up again."""
        clause = ", result=NULL, analyzed_at='', category='', priority=''" if reanalyze else ''
        with self.db:
            self.db.executemany(f"UPDATE mail SET attempts=0, retry_at=0, error=''{clause} WHERE id=?",
                                ((i,) for i in ids))

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
    if row['attempts']:
        return f"{row['attempts']}회 실패"
    return '분석 대기'


def row_view(row):
    """One line of the mail list. Works before analysis, when result is still empty."""
    result = json.loads(row['result']) if row['result'] else {}
    return {'id': row['id'], 'received': local_text(row['received']),
            'sender': row['sender'], 'subject': row['subject'] or '(제목 없음)',
            'category': result.get('category', ''), 'priority': result.get('priority', ''),
            'state': state_of(row), 'error': row['error']}


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
            and matches_state(view['state'], state)]


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
