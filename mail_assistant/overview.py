"""Numbers for the 현황 tab, computed from database rows. No UI, no COM.

The Excel dashboard keeps its formulas; the app counts the same things here so it
can show them without a spreadsheet.
"""
import json
from datetime import timedelta

from .calendar_sheet import collect
from .core import HANDLED, day_bounds, local_text
from .core import workbook_rows
from .dashboard import CATEGORIES, PRIORITIES

DUE_DAYS = 7
RECENT_DAYS = 7
UPCOMING = 8


def analysed(rows):
    return [row for row in rows if row['result']]


def results(rows):
    for row in analysed(rows):
        try:
            yield row, json.loads(row['result'])
        except ValueError:
            continue


def schedule_entries(rows):
    """Reuse the 일정 sheet shape so collect()/month_grid() work unchanged."""
    lines = []
    for row in analysed(rows):
        try:
            lines.extend(workbook_rows(row)['일정'])
        except (ValueError, KeyError, TypeError):
            continue
    return collect(lines)


def deadlines(events, today, within=DUE_DAYS, handled=()):
    """(date, entry) for deadlines from today up to `within` days, handled mails dropped."""
    limit = today + timedelta(days=within)
    return [(day, entry) for day in sorted(events) if today <= day <= limit
            for entry in events[day] if entry.kind != '시작' and entry.mail_id not in handled]


def past_due(events, today, handled=(), limit=UPCOMING):
    """Missed deadlines, nearest first: the 7-day window above would hide them entirely."""
    return [(day, entry) for day in sorted(events, reverse=True) if day < today
            for entry in events[day] if entry.kind != '시작' and entry.mail_id not in handled][:limit]


def counts_by(rows, field, allowed):
    counted = {name: 0 for name in allowed}
    for _, result in results(rows):
        value = result.get(field, '')
        if value in counted:
            counted[value] += 1
    return counted


def recent_days(rows, today, days=RECENT_DAYS):
    counted = {(today - timedelta(days=offset)).isoformat(): 0 for offset in range(days)}
    for row in rows:
        key = local_text(row['received'], '%Y-%m-%d')
        if key in counted:
            counted[key] += 1
    return [(day, counted[day]) for day in sorted(counted)]


def trend(store, account, today, days=RECENT_DAYS):
    """[(day, {collected, analyzed, exported})] oldest first, by Korean calendar day.

    Store.day_counts() existed and was tested but no screen ever asked for it.
    """
    rows = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        rows.append((day.isoformat(), store.day_counts(account, *day_bounds(day))))
    return rows


def review_queue(rows):
    """답변이 필요한데 사람이 초안을 손대지 않은 메일. 카드와 검토 큐가 같은 판정을 쓴다."""
    return [row for row, result in results(rows)
            if result.get('reply_needed') and not (row['draft_edit'] or '').strip()]


def review_pending(rows):
    return len(review_queue(rows))


def overview(rows, today, within=DUE_DAYS):
    handled = {row['id'] for row in rows if row['handled'] == HANDLED}
    events = schedule_entries(rows)
    due = deadlines(events, today, within=within, handled=handled)
    priorities = counts_by(rows, 'priority', [name for name, _ in PRIORITIES])
    return {
        'cards': {
            '미처리 메일': sum(1 for row in analysed(rows) if row['id'] not in handled),
            '긴급·높음': priorities.get('긴급', 0) + priorities.get('높음', 0),
            f'{within}일 내 마감': len(due),
            '검토 전 초안': review_pending(rows),
        },
        'categories': counts_by(rows, 'category', CATEGORIES),
        'priorities': priorities,
        'recent': recent_days(rows, today),
        'upcoming': due[:UPCOMING],
        'past_due': past_due(events, today, handled=handled),
        'within': within,
        'events': events,
        'handled': handled,
        'total': len(rows),
        'waiting': sum(1 for row in rows if not row['result']),
    }
