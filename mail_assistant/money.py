"""메일에서 읽은 금액을, 합계로 세워도 되는 것과 아닌 것으로 가른다. 화면도 COM도 없다.

이 앱에서 가장 위험한 기능이다. 지금까지 분석이 만든 것은 사람이 읽고 판단하는 문장이고,
틀리면 읽는 사람이 알아본다. 합계는 다르다 — 90,000을 900,000으로 읽은 한 줄이 '이번 달
견적 4,350만원'이 되면, 그 숫자는 틀렸다는 것을 스스로 말하지 않는다. 그래서 이 모듈의
규칙은 전부 '세지 않는 쪽'으로 기울어 있다.

넷이다.

1. 통화가 다르면 절대 더하지 않는다. $5,000과 ₩5,000,000을 더한 5,005,000은 숫자가
   아니라 사고다.
2. 숫자로 읽히지 않으면 0이 아니라 **뺀다**. 못 읽은 것을 0으로 세면 합계는 조용히
   작아지고, 작아진 합계는 틀렸다고 말하지 않는다.
3. needs_review가 붙은 것도 뺀다. 모델이 스스로 확신하지 못한 금액이다.
4. 뺀 것이 몇 건인지 언제나 같이 돌려준다. 화면이 그것을 말하지 않을 수 없게 하려고
   합계와 한 묶음으로 둔다 — `totals()`가 제외 건수 없이는 대답하지 않는다.
"""
import json
import re
from datetime import date

from .core import HANDLED, local_text

# 분석이 고를 수 있는 금액의 종류. '기타'가 있는 것은 모델이 억지로 하나를 고르는 것보다
# 낫기 때문이고, 화면은 종류별로 나눠 세므로 기타가 섞여 견적 합계를 흐리지 않는다.
KINDS = ('견적', '청구', '입금', '계약', '기타')
# 통화 코드와 화면에 쓸 기호. '기타'는 코드도 기호도 없는 자리 — 읽기는 했는데 무슨
# 통화인지 모르겠다는 답이고, 합계에서 빠진다.
CURRENCIES = (('KRW', '₩'), ('USD', '$'), ('JPY', '¥'), ('EUR', '€'), ('기타', ''))
CURRENCY_CODES = tuple(code for code, _ in CURRENCIES)
SYMBOLS = dict(CURRENCIES)
# 합계에 넣지 않는 통화. 무엇인지 모르는 돈끼리 더한 수는 아무 질문에도 답하지 않는다.
UNKNOWN_CURRENCY = '기타'
# 숫자만. 쉼표와 공백은 떨어뜨리되 그 밖의 글자가 하나라도 있으면 읽지 못한 것으로 둔다 —
# '약 90,000', '90,000~100,000', '9만'은 전부 사람이 봐야 하는 값이지 추측할 값이 아니다.
DIGITS = re.compile(r'^\d+$')
# 원 단위의 소수점은 실무에서 나오지 않지만 외화는 나온다. 소수 둘까지만 받는다.
DECIMAL = re.compile(r'^\d+\.\d{1,2}$')


def money_value(text):
    """'1,234,000' → 1234000, 읽을 수 없으면 None. 추측은 하지 않는다.

    None과 0은 다르다. 0은 '영 원'이고 None은 '나는 이 글자를 숫자로 읽지 못했다'이며,
    합계에서 빠지는 것은 뒤쪽뿐이다.
    """
    cleaned = str(text or '').replace(',', '').replace(' ', '').strip()
    if DIGITS.match(cleaned):
        return int(cleaned)
    if DECIMAL.match(cleaned):
        return float(cleaned)
    return None


def money_text(value, currency=''):
    """'₩1,234,000'. 기호가 없는 통화는 코드를 앞에 둔다 — 단위 없는 숫자는 금액이 아니다."""
    if value is None:
        return ''
    number = f'{value:,.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else f'{value:,}'
    symbol = SYMBOLS.get(currency, '')
    if symbol:
        return f'{symbol}{number}'
    return f'{number} {currency}'.strip() if currency else number


def entry_of(item, row):
    """result의 money 한 칸을 화면과 합계가 함께 쓰는 한 줄로.

    `value`가 None이거나 `review`가 True인 줄이 '세지 않는 줄'이고, 그 판정을 여기서 한 번
    내린다 — 화면과 합계가 서로 다른 기준으로 같은 줄을 대하면, 화면에 보이는 것과 합계에
    들어간 것이 다르다는 뜻이 된다.
    """
    currency = str(item.get('currency', '') or '').strip().upper()
    if currency not in CURRENCY_CODES:
        # 모르는 코드도, 아예 없는 것도 같은 답이다: 무슨 돈인지 모른다.
        currency = UNKNOWN_CURRENCY
    kind = str(item.get('kind', '') or '').strip()
    value = money_value(item.get('amount'))
    review = bool(item.get('needs_review')) or value is None or currency == UNKNOWN_CURRENCY
    return {
        'mail': row['id'], 'subject': row['subject'] or '(제목 없음)',
        'sender': row['sender'] or '', 'received': row['received'] or '',
        'day': local_text(row['received'], '%Y-%m-%d'),
        'handled': row['handled'] == HANDLED,
        'kind': kind if kind in KINDS else '기타',
        'currency': currency, 'value': value,
        'text': money_text(value, currency) or str(item.get('amount', '') or ''),
        'label': ' '.join(str(item.get('label', '') or '').split()),
        'evidence': ' '.join(str(item.get('evidence', '') or '').split()),
        'order_no': ' '.join(str(item.get('order_no', '') or '').split()),
        'edited': bool(item.get('edited')),
        # 모델이 확신하지 못했거나, 우리가 숫자로 읽지 못했거나, 통화를 모르거나.
        'review': review,
        'index': item.get('index', 0),
        # 분석이 읽은 줄은 제 행이 없다 — (메일, index)가 그 손잡이다. 손으로 적은 줄만
        # `row`를 들고 오고, 화면은 이 한 칸으로 둘을 가른다.
        'row': None,
    }


# 사람이 적은 줄의 index. 한 메일 안에서 적힌 순서를 뒤집어 세는 자리라 음수가 들어갈
# 일이 없고, 이 값은 손으로 적은 줄끼리의 정렬에만 쓰인다.
MANUAL_INDEX = 0


def manual_entry(row, subjects=None):
    """money 표의 한 줄을, 분석이 읽은 줄과 똑같은 모양으로.

    같은 모양이어야 하는 이유는 화면이 아니라 합계다 — totals()가 두 종류의 줄을
    구별할 수 있으면, 그 구별이 언젠가 '손으로 적은 것은 좀 더 믿어도 되지 않나'가
    된다. 판정은 entry_of()의 것 그대로이고, 다만 모델이 붙이는 needs_review 가
    없다: 모델이 스스로 헷갈린 것과 사람이 적어 넣은 것은 다른 일이다. 대신 못 읽은
    숫자와 모르는 통화는 여기서도 그대로 빠진다.
    """
    currency = str(row['currency'] or '').strip().upper()
    if currency not in CURRENCY_CODES:
        currency = UNKNOWN_CURRENCY
    kind = str(row['kind'] or '').strip()
    value = money_value(row['amount'])
    mail_id = str(row['mail_id'] or '')
    day = str(row['day'] or '') or local_text(row['created'], '%Y-%m-%d')
    return {
        'mail': mail_id,
        'subject': (subjects or {}).get(mail_id, ''),
        'sender': '', 'received': day, 'day': day,
        'handled': False,
        'kind': kind if kind in KINDS else '기타',
        'currency': currency, 'value': value,
        'text': money_text(value, currency) or str(row['amount'] or ''),
        'label': ' '.join(str(row['label'] or '').split()),
        'evidence': ' '.join(str(row['evidence'] or '').split()),
        'order_no': '',
        'edited': False,
        'review': value is None or currency == UNKNOWN_CURRENCY,
        'index': MANUAL_INDEX,
        # 지우기와 고치기가 어느 줄인지 알아야 한다. 분석이 읽은 줄은 (메일, index)로
        # 짚지만 이쪽은 제 행이 있고, 그 행의 id 가 유일한 손잡이다.
        'row': row['id'],
    }


def entries(rows, manual=(), subjects=None):
    """분석된 메일에서 금액 줄을 전부, 그리고 사람이 적어 둔 줄을 그 옆에.

    events가 그렇듯 money도 result JSON 안에 산다 — 반복되는 값이라 컬럼이 될 수 없고,
    새 테이블은 analyzed()·reset()·delete() 세 군데와 어긋날 자리를 만든다. 달력이 이미
    같은 방식으로 돌고 있다(overview.schedule_entries).

    `manual`은 그 규칙의 반대쪽이다 — 메일에 적혀 있지 않은 금액(전화로 받은 견적,
    계약서의 숫자)은 저 JSON 에 들어갈 자리가 아예 없다. 두 곳에서 읽어 한 모양으로
    내놓는 것이 여기이고, totals()부터 아래로는 어느 쪽에서 왔는지 알지 못한다.
    """
    found = [manual_entry(row, subjects) for row in manual]
    for row in rows:
        if not row['result']:
            continue
        try:
            result = json.loads(row['result'])
        except ValueError:
            continue
        order_no = str(result.get('order_no', '') or '').strip()
        for index, item in enumerate(result.get('money') or ()):
            if not isinstance(item, dict):
                continue
            found.append(entry_of({**item, 'index': index,
                                   'order_no': item.get('order_no') or order_no}, row))
    # 메일은 새것부터, 한 메일 안에서는 적힌 순서대로 — 한 견적의 공급가액과 부가세가
    # 거꾸로 서면 두 줄이 서로 다른 건처럼 읽힌다.
    found.sort(key=lambda entry: (entry['received'], -entry['index']), reverse=True)
    return found


def totals(found):
    """{통화: {종류: 합계}}와 뺀 건수를, 언제나 한 묶음으로.

    제외 건수를 합계와 떼어 놓지 않는 것이 이 함수의 요점이다: 부르는 쪽이 합계만 집어
    화면에 그리는 일이 생기지 않게 하려고 한 dict에 같이 담는다. '확인 필요 3건은 합계에서
    뺐어요'가 없는 합계는, 이 앱이 하지 않기로 한 종류의 문장이다.
    """
    sums, counts = {}, {}
    skipped = []
    for entry in found:
        if entry['review'] or entry['value'] is None:
            skipped.append(entry)
            continue
        currency = sums.setdefault(entry['currency'], {})
        currency[entry['kind']] = currency.get(entry['kind'], 0) + entry['value']
        counts.setdefault(entry['currency'], {})
        counts[entry['currency']][entry['kind']] = \
            counts[entry['currency']].get(entry['kind'], 0) + 1
    return {'sums': sums, 'counts': counts,
            'counted': len(found) - len(skipped), 'skipped': len(skipped),
            'review': skipped, 'total': len(found)}


def skipped_text(book):
    """'확인 필요 3건은 합계에서 뺐어요', 뺀 것이 없으면 ''."""
    if not book['skipped']:
        return ''
    return f"확인 필요 {book['skipped']}건은 합계에서 뺐어요"


def months(found, limit=12):
    """금액이 있는 달, 최근부터. 고를 수 있는 달만 고르게 하려고 목록으로 준다."""
    seen = []
    for entry in found:
        key = entry['day'][:7]
        if key and key not in seen:
            seen.append(key)
    return seen[:limit]


def in_month(found, month):
    """한 달치. ''이면 전부 — 필터가 걸려 있지 않다는 뜻이다."""
    if not month:
        return list(found)
    return [entry for entry in found if entry['day'].startswith(month)]


def by_kind(found, kind):
    return list(found) if not kind else [entry for entry in found if entry['kind'] == kind]


def month_title(month):
    """'2026년 9월'. strftime을 쓰지 않는다 — 한글이 들어간 포맷은 Windows에서 터진다."""
    try:
        year, _, rest = month.partition('-')
        return f'{int(year)}년 {int(rest)}월'
    except (ValueError, AttributeError):
        return month or '전체 기간'


def trend(found, month_list):
    """[(달, {통화: {종류: 합계}})] 오래된 달부터. 막대 하나가 한 달이다."""
    return [(month, totals(in_month(found, month))) for month in reversed(month_list)]


def main_currency(found):
    """줄 수가 가장 많은 통화. 어느 통화를 먼저 그릴지 정하는 데만 쓴다."""
    counted = {}
    for entry in found:
        if entry['currency'] != UNKNOWN_CURRENCY:
            counted[entry['currency']] = counted.get(entry['currency'], 0) + 1
    if not counted:
        return CURRENCY_CODES[0]
    return max(counted, key=lambda code: (counted[code], -CURRENCY_CODES.index(code)))
