"""Neutralize spreadsheet formulas when exporting user-controlled strings as CSV."""


def safe_csv_cell(value):
    """Preserve real numeric cells; prefix textual formula candidates with a quote."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    return "'" + text if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) else text
