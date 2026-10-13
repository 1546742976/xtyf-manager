"""CNY: decimal HALF_UP per record, integer cents for arithmetic, text for JSON."""
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from flask import request
from werkzeug.exceptions import BadRequest

MAX_CENTS = 2**63 - 1
_NUMBER = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$', re.ASCII)


def to_cents(value, *, positive=False):
    """Normalize one amount once; never multiply a binary float by 100."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('金额必须是非负有限数值')
    raw = str(value).strip()
    if not raw or len(raw) > 128 or not _NUMBER.fullmatch(raw):
        raise ValueError('金额格式无效')
    try:
        with localcontext() as ctx:
            ctx.prec = 160
            amount = Decimal(raw)
            if not amount.is_finite() or amount < 0 or amount > Decimal(MAX_CENTS) / 100:
                raise ValueError('金额必须在 0 至 92233720368547758.07 元之间')
            cents = int(amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) * 100)
    except InvalidOperation as exc:
        raise ValueError('金额格式无效') from exc
    if positive and cents <= 0:
        raise ValueError('金额按分四舍五入后必须大于 0')
    return cents


def format_cents(cents, *, grouping=False):
    """Also supports negative balances and unbounded Python integer totals."""
    if type(cents) is not int:
        raise ValueError('金额分值必须为整数')
    units, fraction = divmod(abs(cents), 100)
    whole = format(units, ',d') if grouping else str(units)
    return ('-' if cents < 0 else '') + whole + f'.{fraction:02d}'


def decimal_amount(value):
    return Decimal(format_cents(to_cents(value)))


def money_fields(cents, key='amount'):
    # Strings avoid loss in JavaScript above Number.MAX_SAFE_INTEGER.
    return {key: format_cents(cents), key + '_cents': str(cents)}


def money_json():
    """Read the original JSON decimal lexeme before Flask converts it to float."""
    try:
        value = json.loads(request.get_data(cache=True), parse_float=Decimal)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest('请求必须是有效 JSON') from exc
    if not isinstance(value, dict):
        raise BadRequest('请求必须是 JSON 对象')
    return value
