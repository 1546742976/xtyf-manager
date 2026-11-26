"""Small shared input bounds; reject invalid input instead of truncating it."""


def valid_password(value):
    """Keep password characters intact; all credential writers share these bounds."""
    return isinstance(value, str) and 8 <= len(value) <= 1024


def clean_text(value, label, max_len, required=True, allow_newlines=True):
    if value is None and not required:
        value = ''
    if not isinstance(value, str):
        raise ValueError(f'{label}必须是文字')
    allowed_controls = '\n\r\t' if allow_newlines else ''
    if any((ord(char) < 32 and char not in allowed_controls) or ord(char) == 127 for char in value):
        raise ValueError(f'{label}不能包含控制字符')
    if len(value) > max_len:
        raise ValueError(f'{label}最多 {max_len} 个字符')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{label}不能为空')
    return value


def parse_int_text(value, label, max_digits=19):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f'{label}必须是整数')
    if isinstance(value, str):
        if not value or len(value) > max_digits or not value.isascii() or not value.isdecimal():
            raise ValueError(f'{label}必须是最多 {max_digits} 位的整数')
    elif value < 0 or value >= 10**max_digits:
        raise ValueError(f'{label}超出允许范围')
    return int(value)
