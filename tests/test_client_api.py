"""Native-client protocol against the suite's temporary databases and real CSRF."""
import json
import os
import sqlite3
import unittest
from contextlib import closing
from uuid import uuid4

from flask.testing import FlaskClient

from test_tasks import app_module


NATIVE = {"X-XTYF-Client-Protocol": "1"}


class TestClientAPI(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        login = self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        })
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.get_json().get('ok'), '合成管理员登录未成功')
        self.client = FlaskClient(app_module.app, use_cookies=True)
        self.username = "client_" + uuid4().hex[:12]
        self.password = "native-client-test-password"
        created = self.admin.post("/api/member", json={
            "name": "客户端测试成员", "username": self.username, "password": self.password,
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        self.member_id = created.get_json()["id"]

    def permission_diagnostic(self, stage, response):
        """Only synthetic IDs/versions/status: never passwords, cookies or tokens."""
        with self.admin.session_transaction() as session:
            actor = session.get('uid')
            version = session.get('session_version')
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            rows = db.execute('SELECT id,role,is_active,session_version,must_change_password FROM members WHERE id IN (?,?)',
                              (actor, self.member_id)).fetchall()
        result = {'test': self.id(), 'stage': stage, 'status': response.status_code,
                  'actor_id': actor, 'session_version': version, 'accounts': rows}
        if os.environ.get('XTYF_TEST_AUTH_TRACE') == '1':
            print('SYNTHETIC_AUTH ' + json.dumps(result), flush=True)
        return result

    def bootstrap(self, client=None):
        return (client or self.client).get("/api/client/bootstrap", headers=NATIVE)

    def post(self, path, data=None, client=None, headers=None):
        client = client or self.client
        token = self.bootstrap(client).headers["X-CSRF-Token"]
        return client.post(path, json={} if data is None else data, headers={
            **NATIVE, "X-CSRF-Token": token, **(headers or {}),
        })

    def login(self, client=None):
        return self.post("/api/client/login", {
            "username": self.username, "password": self.password,
        }, client=client)

    def onboard(self, client=None):
        client = client or self.client
        self.assertEqual(self.login(client).status_code, 200)
        changed = self.post("/api/password", {
            "old": self.password, "new": self.password + "-changed",
        }, client=client)
        self.assertEqual(changed.status_code, 200, changed.get_json())
        self.password += "-changed"

    def test_anonymous_bootstrap_is_complete_and_private(self):
        response = self.bootstrap()
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["protocolVersion"], 1)
        self.assertTrue(data["ok"])
        self.assertFalse(data["authenticated"])
        self.assertIsNone(data["user"])
        self.assertIsNone(data["me"])
        self.assertFalse(any(data["permissions"].values()))
        self.assertEqual(data["statuses"], app_module.STATUSES)
        self.assertEqual(data["priorities"], app_module.PRIORITIES)
        self.assertEqual(data["role_names"], app_module.ROLE_NAMES)
        self.assertEqual(data["theme_preferences"], {
            "theme": "light", "mode": "light", "light_theme": "silver", "dark_theme": "space",
            "light_custom": False, "dark_custom": False,
            "light_accent": "#3266c7", "dark_accent": "#80aaff",
        })
        self.assertEqual(len(data["theme_options"]["light"]), 4)
        self.assertEqual(len(data["theme_options"]["dark"]), 4)
        self.assertEqual(data["csrf_token"], response.headers["X-CSRF-Token"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn(self.username, response.get_data(as_text=True))

    def test_login_rotates_csrf_and_preserves_first_password_gate(self):
        old_token = self.bootstrap().get_json()["csrf_token"]
        response = self.login()
        self.assertTrue(response.get_json()["must_change_password"])
        self.assertEqual(response.get_json()["redirect"], "/account")
        self.assertNotEqual(old_token, response.headers["X-CSRF-Token"])
        data = self.bootstrap().get_json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["me"], data["user"])
        self.assertEqual(data["user"]["id"], self.member_id)
        self.assertTrue(data["user"]["must_change_password"])
        self.assertNotIn("session_version", data["user"])
        self.assertNotIn("password_hash", data["user"])
        self.assertFalse(any(data["permissions"].values()))
        blocked = self.client.get("/api/data", headers=NATIVE)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.get_json()["reason"], "password_change_required")
        old_csrf = self.client.post("/api/client/logout", json={}, headers={
            **NATIVE, "X-CSRF-Token": old_token,
        })
        self.assertEqual(old_csrf.status_code, 403)
        self.assertEqual(old_csrf.get_json()["reason"], "csrf_failed")

    def test_first_password_change_enables_business_api(self):
        self.onboard()
        data = self.bootstrap().get_json()
        self.assertFalse(data["user"]["must_change_password"])
        self.assertEqual(self.client.get("/api/data", headers=NATIVE).status_code, 200)
        self.assertFalse(data["permissions"]["access_funds"])
        self.assertFalse(data["permissions"]["manage_security"])

    def test_permissions_match_roles_and_independent_finance_grants(self):
        self.onboard()
        for role in ("member", "manager", "admin"):
            with self.subTest(role=role):
                changed = self.admin.post(f"/api/member/{self.member_id}/profile", json={"role": role})
                self.assertEqual(changed.status_code, 200, self.permission_diagnostic('profile-' + role, changed))
                logged = self.login()
                self.assertEqual(logged.status_code, 200, self.permission_diagnostic('login-' + role, logged))
                permitted = ["invoices.manage"] + (["funds.review"] if role != "member" else [])
                grant = self.admin.post(f"/api/admin/permissions/{self.member_id}", json={"permissions": permitted})
                self.assertEqual(grant.status_code, 409 if role == "admin" else 200)
                data = self.bootstrap().get_json()
                manager = role in ("manager", "admin")
                self.assertEqual(data["permissions"]["manage_tasks"], manager)
                self.assertEqual(data["permissions"]["manage_members"], manager)
                self.assertEqual(data["permissions"]["access_funds"], manager)
                self.assertEqual(data["can_review_funds"], manager)
                self.assertEqual(data["can_review"], manager)
                self.assertTrue(data["is_finance"])
                self.assertTrue(data["can_manage_invoices"])
                self.assertEqual(data["permissions"]["manage_mail"], role == "admin")
                self.assertEqual(data["permissions"]["manage_security"], role == "admin")
                self.admin.post(f"/api/admin/permissions/{self.member_id}", json={"permissions": []})
                revoked = self.bootstrap().get_json()
                self.assertEqual(revoked["is_finance"], role == "admin")
                self.assertEqual(revoked["can_review_funds"], role == "admin")

    def test_theme_preferences_are_live_and_account_scoped(self):
        self.onboard()
        changed = self.post("/api/account/preferences", {
            "mode": "system", "light_theme": "paw", "dark_theme": "xueli",
        })
        self.assertEqual(changed.status_code, 200)
        data = self.bootstrap().get_json()
        self.assertEqual(data["theme_preferences"], {
            "theme": "light", "mode": "system", "light_theme": "paw", "dark_theme": "xueli",
            "light_custom": False, "dark_custom": False,
            "light_accent": "#3266c7", "dark_accent": "#80aaff",
        })
        self.assertEqual(data["user"]["theme_mode"], "system")
        self.assertNotEqual(self.bootstrap(self.admin).get_json()["user"]["id"], data["user"]["id"])

    def test_logout_from_forced_password_account_is_json_and_clears_session(self):
        self.login()
        response = self.post("/api/client/logout")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        self.assertNotIn("Location", response.headers)
        self.assertFalse(self.bootstrap().get_json()["authenticated"])
        self.assertEqual(self.client.get("/api/data", headers=NATIVE).status_code, 401)
        self.assertEqual(self.post("/api/client/logout").status_code, 200)

    def test_client_and_web_login_share_rate_limit(self):
        credentials = {"username": self.username, "password": "incorrect"}
        for index in range(6):
            path = "/api/client/login" if index % 2 == 0 else "/login"
            token = self.bootstrap().get_json()["csrf_token"]
            response = self.client.post(path, json=credentials, headers={"X-CSRF-Token": token})
            self.assertEqual(response.status_code, 429 if index == 5 else 400)
        self.assertEqual(self.login().status_code, 429)

    def test_missing_invalid_cross_site_csrf_and_non_object_json_are_rejected(self):
        credentials = {"username": self.username, "password": self.password}
        response = self.client.post("/api/client/login", json=credentials, headers=NATIVE)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"], "csrf_failed")
        for headers in ({"X-CSRF-Token": "wrong"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.post("/api/client/login", credentials, headers=headers).status_code, 403)
        for data in ([], None, "invalid"):
            token = self.bootstrap().get_json()["csrf_token"]
            invalid = self.client.post("/api/client/login", data=json.dumps(data),
                                       content_type="application/json", headers={**NATIVE, "X-CSRF-Token": token})
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(invalid.get_json()["reason"], "invalid_payload")

    def test_login_validates_types_and_does_not_accept_form_transport(self):
        for data in ({"username": ["admin"], "password": "x"},
                     {"username": "admin", "password": {"bad": "type"}},
                     {"username": "a" * 65, "password": "x"},
                     {"username": "admin", "password": "p" * 1025}):
            self.assertEqual(self.post("/api/client/login", data).status_code, 400)
        token = self.bootstrap().get_json()["csrf_token"]
        form = self.client.post("/api/client/login", data={"username": self.username, "password": self.password},
                                headers={**NATIVE, "X-CSRF-Token": token})
        self.assertEqual(form.status_code, 400)

    def test_native_download_auth_errors_are_json_and_browser_behavior_is_preserved(self):
        native = self.client.get("/export.csv", headers=NATIVE)
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.get_json()["reason"], "authentication_required")
        browser = self.client.get("/export.csv")
        self.assertEqual(browser.status_code, 302)
        self.assertTrue(browser.headers["Location"].startswith("/login"))
        self.login()
        forced = self.client.get("/export.csv", headers=NATIVE)
        self.assertEqual(forced.status_code, 403)
        self.assertEqual(forced.get_json()["reason"], "password_change_required")

    def test_password_reset_and_deactivation_revoke_client_session(self):
        self.onboard()
        reset = self.admin.post(f"/api/member/{self.member_id}/reset", json={})
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(self.bootstrap().get_json()["authenticated"])
        self.password = reset.get_json()["initial_password"]
        self.assertEqual(self.login().status_code, 200)
        self.assertTrue(self.bootstrap().get_json()["user"]["must_change_password"])
        toggled = self.admin.post(f"/api/member/{self.member_id}/toggle", json={})
        self.assertEqual(toggled.status_code, 200)
        self.assertFalse(self.bootstrap().get_json()["authenticated"])
        self.assertEqual(self.login().status_code, 400)

    def test_persistent_session_cookie_restores_bootstrap_in_another_client(self):
        self.onboard()
        name = app_module.app.config["SESSION_COOKIE_NAME"]
        cookie = self.client.get_cookie(name)
        self.assertIsNotNone(cookie.expires)
        restored = FlaskClient(app_module.app, use_cookies=True)
        restored.set_cookie(name, cookie.value)
        data = self.bootstrap(restored).get_json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["id"], self.member_id)

    def test_login_logout_are_audited_without_credentials(self):
        self.login()
        self.post("/api/client/logout")
        with sqlite3.connect(app_module.DB_PATH) as db:
            rows = db.execute("SELECT action,actor_id,outcome,details FROM security_audit "
                              "WHERE action IN ('client_api.login','client_api.logout') ORDER BY id DESC LIMIT 2").fetchall()
        self.assertEqual(rows[0][:3], ("client_api.logout", self.member_id, "success"))
        self.assertEqual(rows[1][0], "client_api.login")
        self.assertEqual(rows[1][2], "success")
        self.assertNotIn(self.password, str(rows))
        self.assertNotIn(self.username, str(rows))

    def test_safe_login_destination_remains_shared(self):
        self.onboard()
        for destination, expected in (("/tasks?type=daily&project=42", "/tasks?type=daily&project=42"),
                                      ("https://example.org/", "/"), ("//example.org/", "/")):
            response = self.post("/api/client/login", {
                "username": self.username, "password": self.password, "next": destination,
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["redirect"], expected)


if __name__ == "__main__":
    unittest.main()
