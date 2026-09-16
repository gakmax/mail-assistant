"""분석하기 전에 내려놓을 메일을 가리는 규칙. 순수 함수라 Windows 없이 테스트된다.

여기가 category를 보지 않는 이유가 이 모듈이 있는 이유다: category는 분석이 *만드는*
값이라, '공지면 건너뛴다'는 이미 Codex를 한 번 부른 뒤에야 할 수 있는 말이다. 분석 전에
아는 것은 헤더와 본문뿐이고, 그래서 이 파일이 보는 것도 그것뿐이다.

그리고 거르는 것은 광고와 뉴스레터이지 공지가 아니다. `10월 정기 점검 일정 안내`와
`단가 인상 안내의 건`은 둘 다 공지이고 둘 다 마감과 우선순위를 달고 나온다 — 이 앱이
있는 이유에 가까운 메일들이다. 사람이 손으로 쓴 안내와 발송 시스템이 뿌린 소식지를
가르는 것은 낱말이 아니라 **대량 발송 표시**이고, 그것은 헤더에 있다.
"""

# 대량 발송자가 스스로 다는 표준 헤더. 소식지·마케팅 발송 시스템은 수신거부 경로를
# 넣어야 하므로 이것을 달고, 발주서를 손으로 쓰는 사람은 달지 않는다. 낱말 짐작이
# 아니라 보낸 쪽이 스스로 밝힌 사실이라는 점이 이 신호의 값이다.
BULK_HEADERS = ('list-unsubscribe', 'list-id', 'precedence', 'auto-submitted')
# Precedence 가 이 셋 중 하나면 발송자가 '답장을 기대하지 않는 무더기'라고 말한 것이다.
BULK_PRECEDENCE = ('bulk', 'junk', 'list')
# 정보통신망법이 광고성 메일의 제목에 요구하는 표기. 낱말 짐작 중에서는 드물게
# 오탐이 없다 — 붙이는 쪽이 법 때문에 붙이는 것이라 광고가 아닌 메일에는 없다.
AD_MARKS = ('[광고]', '(광고)', '[AD]')

# 화면에 그대로 나가는 사유. 왜 내려놓았는지 말하지 않는 필터는 조용히 메일을 먹는
# 필터이고, 그것이 이 프로젝트가 가장 싫어하는 실패다.
AD_REASON = '광고 메일'
LETTER_REASON = '뉴스레터·구독 메일'
BULK_REASON = '대량 발송 메일'
AUTO_REASON = '자동 발송 메일'


# 설정에 저장되는 키. FIELDS 에 없는 것은 글자를 적는 칸이 아니기 때문이고, notify 와
# 같은 자리를 쓴다 — normalize()가 values 에 있는 키를 그대로 통과시킨다.
SKIP_KEY = 'skip_bulk'


def skip_bulk(config):
    """이 거르기가 켜져 있는가. 키가 아예 없으면 켜짐 — 이 기능이 있는 이유가 곧 기본값이다.

    꺼짐이 '0'과 '' 둘 다인 것은 설정 화면이 스위치를 notify 와 같은 모양으로 적기
    때문이다(`'1' if event.value else ''`). '0'만 보면 스위치를 내려도 꺼지지 않는다.
    """
    values = config or {}
    if SKIP_KEY not in values:
        return True
    return str(values.get(SKIP_KEY, '')).strip() not in ('', '0')


def domain_of(address):
    """'김과장 <kim@daesung.co.kr>' → 'daesung.co.kr'. 없으면 ''.

    core.address_of() 와 같은 일을 하지만 여기에 다시 있는 것은 방향 때문이다 — core 가
    이 모듈을 읽으므로(BULK_HEADERS), 이쪽에서 core 를 부를 수는 없다. 꺾쇠를 먼저
    벗기지 않으면 'daesung.co.kr>' 이 나오고, 그러면 사내 도메인 가드가 영영 안 맞는다.
    """
    text = str(address or '').strip()
    if '<' in text and '>' in text:
        text = text[text.rfind('<') + 1:text.rfind('>')]
    _, _, domain = text.rpartition('@')
    return domain.strip().strip('<>').lower()


def header_of(parsed, name):
    """parse_mail()이 담아 둔 대량 발송 헤더 한 줄, 소문자로."""
    bulk = parsed.get('bulk') if isinstance(parsed, dict) else None
    if not isinstance(bulk, dict):
        return ''
    return str(bulk.get(name, '') or '').strip().lower()


def skip_reason(parsed, own_domain=''):
    """이 메일을 분석하지 않을 이유, 분석할 것이면 ''.

    `own_domain`은 내 회사 도메인이고, 거기서 온 것은 무엇이 달려 있어도 거르지 않는다.
    사내 그룹웨어·인사 공지 시스템이 수신거부 헤더를 다는 일이 실제로 있고, 그때 걸러
    버리면 이 앱이 놓치면 안 되는 바로 그 메일을 놓친다. 오탐 하나의 값이 정탐 백 개의
    값보다 크다는 것이 이 줄의 계산이다.
    """
    if not isinstance(parsed, dict):
        return ''
    own = str(own_domain or '').strip().lower().lstrip('@')
    here = domain_of(parsed.get('sender', ''))
    # endswith 하나로는 evil-monitorapp.com 이 monitorapp.com 으로 통과한다. 하위
    # 도메인(mail.monitorapp.com)은 같은 회사이므로 점을 앞에 두고 따로 본다.
    if own and here and (here == own or here.endswith('.' + own)):
        return ''
    subject = str(parsed.get('subject', '') or '').lstrip()
    if any(subject.upper().startswith(mark) for mark in AD_MARKS):
        return AD_REASON
    if header_of(parsed, 'list-unsubscribe') or header_of(parsed, 'list-id'):
        return LETTER_REASON
    if header_of(parsed, 'precedence') in BULK_PRECEDENCE:
        return BULK_REASON
    if header_of(parsed, 'auto-submitted').startswith('auto'):
        return AUTO_REASON
    return ''


def skipped_text(count):
    """한 주기에 내려놓은 것을 실행 기록에 남기는 한 줄. 0이면 아무 말도 하지 않는다."""
    return f'광고·뉴스레터 {count}건은 분석하지 않았어요' if count else ''
