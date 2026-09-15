import json
import time
from datetime import date, datetime

from .core import NO_RETRY, Store, account_key, now
from .excel import Excel, error_detail
from .overview import briefing_due, briefing_input, trend
from .report import remember_secret, report
from .services import (BODY_LIMIT, THREAD_TURNS, Unanalyzable, analyze_many, analyze_one,
                       briefing, check_login, context_size, fetch_mail, group_mails,
                       prepare, read_password, thread_context)

# A failed briefing waits this long, rather than retrying on every 180-second cycle.
BRIEF_BACKOFF = 1800
# 한 메일이 이만큼 실패하면 더 묻지 않는다. 전에는 상한이 없어서, 240초 안에 끝나지 않는
# 한 통이 매시간 같은 값을 치르며 영원히 돌았다 — 백오프는 한 시간에서 멈추고 attempts는
# 아무것도 하지 않았다. 다만 이 상한이 한도 소진을 메일 전체의 포기로 바꿔서는 안 되므로,
# 포기는 (1) 혼자 분석하다 실패했고 (2) 그 메일이 마지막으로 실패한 뒤 다른 메일은 분석에
# 성공했을 때만 일어난다 — 즉 Codex는 멀쩡한데 이 메일만 안 되는 것이 확인된 때만.
MAX_ATTEMPTS = 5
GIVE_UP = (f'분석이 {MAX_ATTEMPTS}회 실패해 더 시도하지 않습니다. '
           '메일을 열어 다시 분석을 누르면 한 번 더 시도합니다.')


def subject_of(row):
    return (row['subject'] or '(제목 없음)')[:40]


def connect_detail(exc, password):
    """Keep the real cause visible, without echoing the mail password."""
    detail = f'{type(exc).__name__}: {exc}'.strip()
    if password:
        detail = detail.replace(password, '***')
    return detail[:300]


def write_briefing(store, account, config, today):
    """Build the day's briefing and store it. Called only when briefing_due() agrees."""
    payload = briefing_input(store.page(account), today,
                             events=store.events(account),
                             trend_rows=trend(store, account, today))
    store.save_briefing(account, today.isoformat(), briefing(payload, config))


def run(config, directory, stop, notify, wake=None):
    store = Store(directory / 'mail.db')
    account = account_key(config)
    excel = Excel(config['workbook'])
    next_analysis = 0
    next_briefing = 0
    # A crash mid-analysis leaves the marker behind, and a mail stuck on '분석 중' is
    # a lie the screens have no way to notice. Every start clears it.
    store.mark_analyzing(account, '')
    try:
        while not stop.is_set():
            messages = []
            password = None
            try:
                password = read_password(config['email'])
                remember_secret(password)
                messages.append(fetch_mail(config, store, password))
                store.set_meta('last_fetch:' + account, now())
            except Exception as exc:
                remember_secret(password)
                report('메일 연결 실패', exc, f"서버 {config['host']}:{config['port']}")
                messages.append('메일 연결 실패: 계정·메일 전용 비밀번호·POP3 설정·네트워크를 확인하세요. — '
                                + connect_detail(exc, password))
            pending = store.pending(account, time.time())
            if pending and time.time() >= next_analysis and not stop.is_set():
                try:
                    check_login()
                    # 파싱부터 먼저. 분석할 수 없는 메일은 Codex를 한 번도 부르지 않고
                    # 여기서 내려놓고, 남은 것만 크기와 실패 횟수를 들고 묶음으로 간다.
                    ready, entries = {}, []
                    for row in pending:
                        try:
                            parsed, sent = prepare(row)
                        except Unanalyzable as exc:
                            # Nothing a retry can change, so this mail is put down where
                            # it is: no global backoff, because the queue behind it is
                            # fine, and no crash report, because a report that arrives
                            # again every hour for ever is one the reader stops reading.
                            # The mail says why on its own row, and 분석 실패 counts it.
                            store.failed(row['id'], str(exc), NO_RETRY)
                            messages.append(f'분석 제외: {subject_of(row)} — {exc}')
                            continue
                        # 이 메일이 속한 대화의 앞선 요약. 본문이 아니라 analyze()가
                        # 이미 값을 치른 답이고, 그래서 한 통에 700자 남짓이다.
                        turns = thread_context(store.thread_before(
                            account, row['thread'], row['received'], THREAD_TURNS))
                        if turns:
                            sent['thread'] = turns
                        ready[row['id']] = (row, parsed, sent)
                        entries.append({'id': row['id'], 'attempts': row['attempts'],
                                        # 대화 맥락도 한 번에 보내는 글자다. 예산에서
                                        # 빼지 않으면 묶음이 조용히 BATCH_CHARS를 넘는다.
                                        'size': len(sent['body']) + context_size(turns)})
                    seen = 0
                    for group in group_mails(entries):
                        if stop.is_set():
                            break
                        rows = [ready[ident][0] for ident in group]
                        # Which mail, and how far in: '분석 중' alone left the user
                        # unable to tell a slow analysis from a stuck one.
                        span = (f'{seen + 1}' if len(group) == 1
                                else f'{seen + 1}-{seen + len(group)}')
                        what = (subject_of(rows[0]) if len(group) == 1
                                else f'{len(group)}통 묶음: ' + subject_of(rows[0]))
                        notify(f'메일 분석 중 {span}/{len(entries)}: {what}'
                               ' — 중지하면 현재 분석이 끝난 뒤 멈춥니다.')
                        # The screens read this, not the log line above: a list is not
                        # watching the log, and '분석 대기' for four minutes reads as a
                        # mail nobody has started on. A whole batch carries it.
                        store.mark_analyzing(account, group)
                        seen += len(group)
                        try:
                            if len(group) == 1:
                                found = {group[0]: analyze_one(ready[group[0]][2], config)}
                            else:
                                found = analyze_many([(ident, ready[ident][2])
                                                      for ident in group], config)
                        except Exception as exc:
                            report('분석 실패', exc,
                                   f"{len(group)}건, {rows[0]['attempts'] + 1}번째 시도")
                            # Back off globally too, so rate limits don't trigger repeated calls.
                            delay = min(3600, 300 * 2 ** min(rows[0]['attempts'], 4))
                            next_analysis = time.time() + delay
                            waits = 0
                            for row in rows:
                                # 포기는 이 메일만의 실패가 확인된 때뿐이다 — 혼자 갔고,
                                # 지난 실패 뒤에 다른 메일은 분석에 성공했을 때.
                                if (len(group) == 1 and row['attempts'] + 1 >= MAX_ATTEMPTS
                                        and store.analyzed_since(account, row['failed_at'])):
                                    store.failed(row['id'], GIVE_UP, NO_RETRY)
                                    messages.append(f'분석 포기: {subject_of(row)} — ' + GIVE_UP)
                                    continue
                                store.failed(row['id'], '분석 실패: 로그인·한도·본문 형식을 확인하세요.',
                                             next_analysis)
                                waits += 1
                            if waits:
                                # 묶음이면 다섯 통이 같은 문장을 다섯 번 쓰게 된다. 한 번만.
                                messages.append(f'분석 대기: {waits}건, 약 {delay // 60}분 후 재시도합니다.')
                            break
                        finally:
                            store.mark_analyzing(account, '')
                        for ident in group:
                            row, parsed, _ = ready[ident]
                            result = found.get(ident)
                            if result is None:
                                # 묶음에서 빠져 돌아왔다. 전체 백오프 없이, 다음 주기에
                                # 혼자 다시 간다 — group_mails()가 attempts로 가른다.
                                store.failed(ident, '분석 결과가 오지 않아 한 통씩 다시 시도합니다.', 0)
                                messages.append(f'다시 시도 예정: {subject_of(row)}')
                                continue
                            store.analyzed(ident, parsed, result)
                            if parsed.get('clipped'):
                                # Not silent anywhere: the mail carries the notice and
                                # 실행 says it too, because a clipped analysis reads
                                # exactly like a whole one.
                                messages.append(f'본문이 길어 앞부분 {BODY_LIMIT:,}자만 분석: '
                                                f'{subject_of(row)}')
                except Exception as exc:
                    report('Codex 로그인 확인 실패', exc)
                    next_analysis = time.time() + 300
                    messages.append('Codex ChatGPT 로그인을 확인하세요. 5분 후 재시도합니다.')
            waiting = store.unexported(account)
            counts = store.counts(account)
            status = {
                '마지막 메일 확인': store.get_meta('last_fetch:' + account) or '연결 대기',
                '이번 엑셀 반영': now(),
                '분석 대기': counts['pending'], '엑셀 반영 대기(이번 반영 전)': counts['waiting'],
                '안내': ' / '.join(messages),
            }
            try:
                excel.update(waiting, status)
                store.exported([row['id'] for row in waiting])
                store.set_meta('last_export:' + account, now())
                messages.append(f'엑셀 반영 완료 ({len(waiting)}건)')
            except Exception as exc:
                report('엑셀 반영 실패', exc, f'대기 {len(waiting)}건')
                messages.append('엑셀 반영 대기: ' + error_detail(exc))
            # Last in the cycle, and only with the analysis queue empty: this shares the
            # one Codex slot with analyze(), and the morning's mail is what that slot
            # is for. The date stamp in meta is what makes it daily rather than a
            # scheduler — the loop already wakes every `interval`.
            today = date.today()
            asked = store.get_meta('briefing_ask:' + account) == '1'
            if asked:
                # Cleared before the attempt: a request that fails must not re-fire
                # every half hour for ever with nobody having asked again.
                store.set_meta('briefing_ask:' + account, '')
            if (counts['total'] and time.time() >= next_briefing and not stop.is_set()
                    and briefing_due(store.get_meta('briefing:' + account), today,
                                     datetime.now().hour, counts['pending'], asked)):
                try:
                    notify('오늘의 AI 브리핑을 만드는 중입니다.')
                    write_briefing(store, account, config, today)
                    store.set_meta('briefing:' + account, today.isoformat())
                    messages.append('AI 브리핑을 새로 만들었습니다.')
                except Exception as exc:
                    report('브리핑 생성 실패', exc)
                    next_briefing = time.time() + BRIEF_BACKOFF
                    messages.append(f'AI 브리핑 실패: 약 {BRIEF_BACKOFF // 60}분 후 다시 시도합니다.')
            snapshot = {**status, **store.counts(account), 'message': ' / '.join(messages)}
            (directory / 'status.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
            notify(snapshot['message'])
            if wake is None:
                stop.wait(config['interval'])
            else:
                # '지금 확인' sets this, and halt() sets it too so stopping stays immediate.
                wake.wait(config['interval'])
                wake.clear()
    finally:
        try:
            store.mark_analyzing(account, '')
        except Exception as exc:
            report('분석 표시 정리 실패', exc)
        store.db.close()
