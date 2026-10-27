"""Shared asset body limits, enforced before CSRF/audit parses the body."""


def asset_request_limit(endpoint):
    if endpoint in {"asset_bom.import_sheet", "asset_bom.preview"}:
        return 2 * 1024 * 1024 + 16 * 1024  # file plus multipart envelope
    if endpoint in {"asset_bom.match_rows", "asset_bom.commit"}:
        return 512 * 1024  # bounded 200-row JSON batches
    return 16 * 1024
