"""Versioned initialization and authentication for the bundled app interface.

The desktop and Android clients reuse the website's cookie session, CSRF guard,
account records and authorization checks. No remote HTML is needed to start the
local UI, including when a first-login password change is required.
"""
from flask import Blueprint, jsonify, request, session

from common import ROLE_NAMES, current_user
from modules.funds import can_review
from modules.invoice_validation import STATUSES as INVOICE_STATUSES
from modules.security import csrf_token, has_permission
from modules.workspace_settings import DARK_THEMES, LIGHT_THEMES, THEME_MODES, preferences

PROTOCOL_VERSION = 1
PUBLIC_USER_FIELDS = (
    "id", "name", "username", "role", "group_name", "must_change_password",
    "ui_theme", "theme_mode", "light_theme", "dark_theme",
)


def create_client_blueprint(authenticate, statuses, priorities):
    """Receive the same login handler used by /login, without circular imports."""
    bp = Blueprint("client_api", __name__, url_prefix="/api/client")

    @bp.get("/bootstrap")
    def bootstrap():
        user = current_user()
        public_user = {key: user[key] for key in PUBLIC_USER_FIELDS} if user else None
        if public_user:
            public_user["must_change_password"] = bool(public_user["must_change_password"])
        ready = bool(user and not user["must_change_password"])
        manager = bool(ready and user["role"] in ("admin", "manager"))
        admin = bool(ready and user["role"] == "admin")
        review = bool(ready and can_review(user))
        invoices = bool(ready and has_permission("invoices.manage", user))
        return jsonify(
            ok=True,
            protocolVersion=PROTOCOL_VERSION,
            authenticated=user is not None,
            user=public_user,
            me=public_user,
            permissions={
                "manage_tasks": manager,
                "manage_members": manager,
                "access_funds": manager,
                "review_funds": review,
                "manage_invoices": invoices,
                "manage_mail": admin,
                "manage_security": admin,
            },
            role_names=ROLE_NAMES,
            statuses=list(statuses),
            priorities=list(priorities),
            invoice_statuses=list(INVOICE_STATUSES),
            theme_preferences=preferences(user),
            theme_options={
                "modes": sorted(THEME_MODES),
                "light": sorted(LIGHT_THEMES),
                "dark": sorted(DARK_THEMES),
            },
            can_review_funds=review,
            can_review=review,
            can_manage_invoices=invoices,
            is_finance=invoices,
            csrf_token=csrf_token(),
        )

    @bp.post("/login")
    def login():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(ok=False, error="请求必须是 JSON 对象", reason="invalid_payload"), 400
        return authenticate(data)

    @bp.post("/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    return bp
