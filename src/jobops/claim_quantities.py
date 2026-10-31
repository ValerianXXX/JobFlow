"""Literal quantity checks; neither unit conversion nor semantic entailment.

Retain coefficients, explicit scale, sign and recognized adjacent units together.
Only an otherwise identical sentence establishes a metric-order contradiction.
Unknown units, paraphrased relations and unsupported scripts still need review.
"""
from __future__ import annotations

import re
from collections import defaultdict


NUMBER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[$£€¥]\s*)?"
    r"(?:\d{1,3}(?:(?:,[ \t]*|[ '\u00a0\u2009\u202f])\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?!\d)"
)


def canonical_number(value: str) -> str:
    cleaned = re.sub(r"[$£€¥, '\t\u00a0\u2009\u202f]", "", value)
    integer, separator, fraction = cleaned.partition(".")
    integer = integer.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return integer + ("." + fraction if separator and fraction else "")


CURRENCIES = {
    **{code: code.upper() for code in ('usd', 'eur', 'gbp', 'cad', 'aud', 'cny', 'rmb', 'jpy', 'hkd')},
    'rmb': 'CNY', 'us dollars': 'USD', 'u.s. dollars': 'USD', '美元': 'USD', '欧元': 'EUR',
    '英镑': 'GBP', '人民币': 'CNY', '日元': 'JPY', '加元': 'CAD', '澳元': 'AUD', '港元': 'HKD',
    # An unqualified dollar/pound/yen sign is not an inferred jurisdiction.
    '$': 'DOLLAR', 'dollars': 'DOLLAR', 'dollar': 'DOLLAR', '£': 'POUND', '€': 'EUR',
    '¥': 'YEN_OR_YUAN', '元': 'YUAN',
}
SCALES = {'thousand': '1000', 'million': '1000000', 'billion': '1000000000',
          '万': '10000', '亿': '100000000', 'k': '1000', 'm': '1000000', 'b': '1000000000'}
UNITS = {
    'percentage point': 'PERCENTAGE_POINT', 'percentage points': 'PERCENTAGE_POINT', '个百分点': 'PERCENTAGE_POINT',
    '百分点': 'PERCENTAGE_POINT', 'basis point': 'BASIS_POINT', 'basis points': 'BASIS_POINT', 'bps': 'BASIS_POINT',
    'percent': 'PERCENT', 'per cent': 'PERCENT', '%': 'PERCENT',
    **{word: 'MILLISECOND' for word in ['millisecond', 'milliseconds', 'ms', '毫秒']},
    **{word: 'MICROSECOND' for word in ['microsecond', 'microseconds', '微秒']},
    **{word: 'SECOND' for word in ['second', 'seconds', 'sec', 'secs', '秒']},
    **{word: 'MINUTE' for word in ['minute', 'minutes', 'min', 'mins', '分钟']},
    **{word: 'HOUR' for word in ['hour', 'hours', 'hr', 'hrs', '小时', '小時']},
    **{word: 'DAY' for word in ['day', 'days', '天']},
    **{word: 'WEEK' for word in ['week', 'weeks', '周']},
}


def _alternatives(values) -> str:
    return '|'.join(re.escape(value).replace(r'\ ', r'\s+') for value in sorted(values, key=len, reverse=True))


_CURRENCY_BEFORE = re.compile(r'(?<![a-z])(' + _alternatives(CURRENCIES) + r')\s*$')
_CURRENCY_AFTER = re.compile(r'^\s*(' + _alternatives(CURRENCIES) + r')(?![a-z])')
_SCALE_AFTER = re.compile(r'^\s*(' + _alternatives({key: value for key, value in SCALES.items() if len(key) > 1 or key in '万亿'}) + r')(?![a-z])')
_SHORT_SCALE_AFTER = re.compile(r'^\s*([kmb])(?![a-z])')
_UNIT_AFTER = re.compile(r'^\s*(' + _alternatives(UNITS) + r')(?![a-z])')
_PLAIN_LIST_JOIN = re.compile(r'(?:(?:and|or|和|及|与|或)|[,、&\s])*')


def _key(value: str) -> str:
    return ' '.join(value.split())


def _quantities(text: str):
    for match in NUMBER_TOKEN_RE.finditer(text):
        raw = match.group(0)
        # Unit tokens are adjacent; do not repeatedly copy the whole document
        # around every occurrence. Unbounded prefixes must not create quadratic
        # allocation as a long cited range gains more quantities.
        before = text[max(0, match.start() - 40):match.start()]
        after = text[match.end():match.end() + 80]
        sign = '-1' if before.endswith(('-', '−')) and (len(before) < 2 or not before[-2].isalnum()) else '1'
        currency_match = _CURRENCY_BEFORE.search(before[:-1] if sign == '-1' else before)
        currency = CURRENCIES[_key(currency_match[1])] if currency_match else None
        symbol = raw[:1] if raw[:1] in '$£€¥' else None
        if symbol:
            compatible = {'$': {'USD', 'CAD', 'AUD', 'HKD', 'DOLLAR'}, '£': {'GBP', 'POUND'},
                          '€': {'EUR'}, '¥': {'JPY', 'CNY', 'YEN_OR_YUAN'}}[symbol]
            symbol_currency = CURRENCIES[symbol]
            currency = (symbol_currency if currency is None else currency if currency in compatible
                        else currency + '/' + symbol_currency)
        scale_match = _SCALE_AFTER.match(after) or (_SHORT_SCALE_AFTER.match(after) if currency else None)
        scale = SCALES[scale_match[1]] if scale_match else '1'
        suffix = after[scale_match.end():] if scale_match else after
        currency_after = _CURRENCY_AFTER.match(suffix)
        if currency_after:
            # Retain both explicit identities if the source itself is conflicting.
            other = CURRENCIES[_key(currency_after[1])]
            currency = other if currency is None or currency == other else currency + '/' + other
            suffix = suffix[currency_after.end():]
        unit_match = _UNIT_AFTER.match(suffix)
        unit = UNITS[_key(unit_match[1])] if unit_match else None
        if before.rstrip().endswith('百分之'):
            unit = unit or 'PERCENT'
        yield canonical_number(raw), (scale, sign, currency, unit)


def _sentence_shapes(text: str):
    for clause in re.split(r'[;；。!?！？]|\.(?=\s|$)', text):
        matches = list(NUMBER_TOKEN_RE.finditer(clause))
        if len(matches) < 2:
            continue
        if all(_PLAIN_LIST_JOIN.fullmatch(clause[a.end():b.start()]) for a, b in zip(matches, matches[1:])):
            continue  # A plain enumerated list has no per-value metric labels.
        shape = _key(NUMBER_TOKEN_RE.sub('<NUMBER>', clause)).strip()
        yield shape, tuple(canonical_number(match.group(0)) for match in matches)


def numeric_context_report(statement: str, source_excerpt: str) -> dict[str, int]:
    """Inputs have already received the caller's existing Unicode/wrap handling.

    Missing suffixes may be completed by that caller's bounded wrap logic. An
    explicit contrary unit, scale, sign or literal metric order cannot borrow
    evidence from a neighboring action. No private content leaves this report.
    """
    statement, source_excerpt = statement.casefold(), source_excerpt.casefold()
    known = defaultdict(set)
    for value, context in _quantities(source_excerpt):
        known[value].add(context)
    unsupported = conflicts = 0
    for value, context in _quantities(statement):
        source_contexts = known.get(value)
        # Existing numeric-presence validation owns entirely absent coefficients.
        if source_contexts and context not in source_contexts:
            unsupported += 1
            conflicts += any(item != ('1', '1', None, None) for item in source_contexts)
    shapes = defaultdict(set)
    for shape, values in _sentence_shapes(source_excerpt):
        shapes[shape].add(values)
    for shape, values in _sentence_shapes(statement):
        if shape in shapes and values not in shapes[shape]:
            unsupported += 1
            conflicts += 1
    return {'unsupported_numeric_context_count': unsupported, 'explicit_numeric_context_conflict_count': conflicts}
