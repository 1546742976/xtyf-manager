"""GitHub collaboration entry page behavior through the Flask surface."""
import unittest
from uuid import uuid4

from test_tasks import app_module


DEFAULT_REPOSITORY = "SHM-white/xtyf-manager"


class TestDiscussionPage(unittest.TestCase):
    def setUp(self):
        # Given: an authenticated administrator and an isolated repository setting.
        self.client = app_module.app.test_client()
        login = self.client.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        })
        self.assertTrue(login.get_json()["ok"])
        self.previous_repository = app_module.app.config.get("GITHUB_REPOSITORY")

    def tearDown(self):
        if self.previous_repository is None:
            app_module.app.config.pop("GITHUB_REPOSITORY", None)
        else:
            app_module.app.config["GITHUB_REPOSITORY"] = self.previous_repository

    def test_requires_login(self):
        # Given: a visitor without a session.
        visitor = app_module.app.test_client()

        # When: the visitor opens the discussion page.
        response = visitor.get("/discussions")

        # Then: the existing login boundary redirects the visitor.
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_renders_default_repository_links(self):
        # Given: the documented default repository configuration.
        app_module.app.config["GITHUB_REPOSITORY"] = DEFAULT_REPOSITORY

        # When: a signed-in user opens the collaboration page.
        response = self.client.get("/discussions")

        # Then: the page offers only derived GitHub entry points.
        content = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("https://github.com/SHM-white/xtyf-manager/discussions", content)
        self.assertIn("https://github.com/SHM-white/xtyf-manager/issues/new/choose", content)
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener noreferrer"', content)

    def test_sidebar_entry_is_available_to_each_logged_in_role(self):
        # Given: one account for every non-administrator workspace role.
        for role in ("manager", "member"):
            with self.subTest(role=role):
                username = f"discussion_{role}_{uuid4().hex[:12]}"
                password = "discussion-test-password"
                created = self.client.post("/api/member", json={
                    "name": f"协作入口{role}",
                    "username": username,
                    "password": password,
                    "role": role,
                })
                self.assertTrue(created.get_json()["ok"])
                user_client = app_module.app.test_client()
                login = user_client.post("/login", json={
                    "username": username, "password": password,
                })

                # When: each account opens the normal discussion route.
                response = user_client.get("/discussions")

                # Then: the common workspace sidebar exposes the same entry.
                self.assertTrue(login.get_json()["ok"])
                self.assertEqual(response.status_code, 200)
                self.assertIn('href="/discussions"', response.get_data(as_text=True))

    def test_shows_guidance_when_repository_configuration_is_empty_or_invalid(self):
        # Given: repository settings that cannot form a GitHub owner/repository path.
        for repository in ("", "owner/repository/extra", "https://github.com/owner/repository"):
            with self.subTest(repository=repository):
                app_module.app.config["GITHUB_REPOSITORY"] = repository

                # When: a signed-in user opens the collaboration page.
                response = self.client.get("/discussions")

                # Then: it explains the configuration requirement without inventing a link.
                content = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn("请联系管理员配置 GITHUB_REPOSITORY", content)
                self.assertNotIn("https://github.com/owner/repository", content)


if __name__ == "__main__":
    unittest.main()
