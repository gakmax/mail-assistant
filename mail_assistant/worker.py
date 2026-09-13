import json
import time

from .core import Store, account_key, now
from .excel import Excel, error_detail
from .report import remember_secret, report
from .services import analyze, check_login, fetch_mail, read_password


def connect_detail(exc, password):
    """Keep the real cause visible, without echoing the mail password."""
    detail = f'{type(exc).__name__}: {exc}'.strip()
    if password:
        detail = detail.replace(password, '***')
    return detail[:300]


def run(config, directory, stop, notify, wake=None):
    store = Store(directory / 'mail.db')
    account = account_key(config)
    excel = Excel(config['workbook'])
    next_analysis = 0
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
                    for index, row in enumerate(pending, 1):
                        if stop.is_set():
                            break
                        # Which mail, and how far in: '분석 중' alone left the user
                        # unable to tell a slow analysis from a stuck one.
                        subject = (row['subject'] or '(제목 없음)')[:40]
                        notify(f'메일 분석 중 {index}/{len(pending)}: {subject}'
                               ' — 중지하면 현재 분석이 끝난 뒤 멈춥니다.')
                        try:
                            parsed, result = analyze(row, config)
                            store.analyzed(row['id'], parsed, result)
                        except Exception as exc:
                            report('분석 실패', exc, f"{row['attempts'] + 1}번째 시도")
                            # Back off globally too, so rate limits don't trigger repeated calls.
                            delay = min(3600, 300 * 2 ** min(row['attempts'], 4))
                            next_analysis = time.time() + delay
                            store.failed(row['id'], '분석 실패: 로그인·한도·본문 형식을 확인하세요.', next_analysis)
                            messages.append(f'분석 대기: 약 {delay // 60}분 후 재시도합니다.')
                            break
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
        store.db.close()
