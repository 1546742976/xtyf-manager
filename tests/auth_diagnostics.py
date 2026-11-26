"""Opt-in diagnostics for synthetic fixtures. Never prints cookie/token/password."""
import json
import time
from datetime import datetime, timezone

from itsdangerous import BadSignature


def install(app):
    interface = app.session_interface
    original = interface.open_session

    def traced_open(application, request):
        session = original(application, request)
        cookie = request.cookies.get(interface.get_cookie_name(application))
        if cookie and not session.get('uid'):
            serializer = interface.get_signing_serializer(application)
            try:
                serializer.loads(cookie, max_age=int(application.permanent_session_lifetime.total_seconds()))
            except BadSignature as exc:
                signed = getattr(exc, 'date_signed', None)
                print('SYNTHETIC_COOKIE ' + json.dumps({
                    'path': request.path, 'error_type': type(exc).__name__,
                    'signed_at': signed.isoformat() if isinstance(signed, datetime) else None,
                    'checked_at': datetime.now(timezone.utc).isoformat(),
                    'wall_seconds': time.time(),
                }), flush=True)
        return session

    interface.open_session = traced_open
