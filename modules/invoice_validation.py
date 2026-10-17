"""Validate writable invoice metadata before any file or JSON mutation."""
import re
from datetime import datetime

STATUSES = ('未报销', '正在报销', '已报销')
TEXT_FIELDS = ('category', 'category_override', 'notes', 'vendor', 'confirmed_vendor',
               'invoice_number', 'original_name', 'submitter', 'tmp_path', 'id')


def validate_fields(data):
    if not isinstance(data, dict):
        return '条目必须是 JSON 对象'
    for field in TEXT_FIELDS:
        if field in data and not isinstance(data[field], str):
            return f'{field} 必须是字符串'
    for field in ('date', 'confirmed_date', 'reimbursed_date'):
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            return f'{field} 必须是日期字符串'
        if value:
            try:
                if datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d') != value:
                    raise ValueError()
            except ValueError:
                return f'{field} 必须是有效的 YYYY-MM-DD 日期'
    if 'month' in data:
        month = data['month']
        if not isinstance(month, str) or (month != '未归类' and not re.fullmatch(
                r'[0-9]{4}\.(?:0?[1-9]|1[0-2])', month)):
            return '月份必须是有效的 YYYY.M 或未归类'
    if 'status' in data and data['status'] not in STATUSES:
        return '无效报销状态'
    if 'tags' in data and (not isinstance(data['tags'], list)
                           or any(not isinstance(tag, str) for tag in data['tags'])):
        return 'tags 必须是字符串数组'
    if 'allow_duplicate' in data and type(data['allow_duplicate']) is not bool:
        return 'allow_duplicate 必须是布尔值'
    return None
