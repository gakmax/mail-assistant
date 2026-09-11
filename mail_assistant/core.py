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
                     ('sender', "TEXT NOT NULL DEFAULT ''"))
        with self.db:
            for column, declaration in additions:
                if column not in present:
                    self.db.execute(f'ALTER TABLE mail ADD COLUMN {column} {declaration}')
        self.backfill_headers()

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
            self.db.execute('UPDATE mail SET parsed=?, result=?, error=\'\', analyzed_at=? WHERE id=?',
                            (json.dumps(parsed, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
                             now(), ident))

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
        """Newest first, without the raw blob. Filtering happens in filter_rows()."""
        return self.db.execute(f'SELECT {self.LIST_COLUMNS} FROM mail WHERE account=? '
                               'ORDER BY received DESC LIMIT ?', (account, limit)).fetchall()

    def detail(self, ident):
        return self.db.execute('SELECT * FROM mail WHERE id=?', (ident,)).fetchone()

    def set_handled(self, ident, state):
        with self.db:
            self.db.execute('UPDATE mail SET handled=? WHERE id=?', (state, ident))

    def set_draft(self, ident, text):
        with self.db:
            self.db.execute('UPDATE mail SET draft_edit=? WHERE id=?', (text, ident))

    def reset(self, ids, reanalyze=False):
        """Clear the backoff so the next cycle picks these up again."""
        clause = ", result=NULL, analyzed_at=''" if reanalyze else ''
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


def local_text(stamp, pattern='%m-%d %H:%M'):
    """UTC ISO string -> Korean local time for display."""
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(KST).strftime(pattern)
    except (TypeError, ValueError):
        return str(stamp or '')


def state_of(row):
    if row['handled'] == HANDLED:
        return HANDLED
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


def filter_rows(views, query='', state=''):
    text = query.strip().lower()
    return [view for view in views
            if (not text or text in view['subject'].lower() or text in view['sender'].lower())
            and (not state or view['state'] == state)]


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
