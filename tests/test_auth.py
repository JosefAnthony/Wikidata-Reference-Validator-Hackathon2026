"""Tests for Wikimedia sign-in (OAuth 2.0 authorization-code flow)."""

import base64
import hashlib
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import app


class FakeResponse:
    """Just enough of a requests.Response for the code under test."""

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class SafeNextTests(unittest.TestCase):
    def test_relative_paths_are_allowed(self):
        self.assertEqual(app.safe_next("/use"), "/use")

    def test_external_and_scheme_relative_urls_fall_back_home(self):
        with app.app.test_request_context():
            home = app.url_for("home")
            for target in ("https://evil.test", "//evil.test", "evil", None, 42):
                self.assertEqual(app.safe_next(target), home)


class LoginPageTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_login_page_offers_wikimedia_sign_in(self):
        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", "shh"):
            response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sign in with Wikimedia", response.data)
        self.assertIn(b"/oauth/login", response.data)

    def test_login_page_explains_missing_configuration(self):
        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", ""), \
                patch.object(app, "WIKIMEDIA_OAUTH_USE_PKCE", False):
            response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"not configured", response.data)
        self.assertNotIn(b"Sign in with Wikimedia", response.data)

    def test_pkce_opt_in_counts_as_configured(self):
        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", ""), \
                patch.object(app, "WIKIMEDIA_OAUTH_USE_PKCE", True):
            self.assertTrue(app.oauth_configured())

    def test_missing_client_id_is_not_configured(self):
        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_ID", ""):
            self.assertFalse(app.oauth_configured())

    def test_error_message_is_shown(self):
        response = self.client.get("/login?error=denied")

        self.assertIn(b"You cancelled the Wikimedia sign-in.", response.data)

    def test_signed_in_user_is_sent_home(self):
        with self.client.session_transaction() as sess:
            sess["user"] = {"username": "Josef"}

        response = self.client.get("/login")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.headers["Location"]).path, "/")


class OAuthLoginTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_redirects_to_authorize_with_state(self):
        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", "shh"):
            response = self.client.get("/oauth/login?next=/use")

        self.assertEqual(response.status_code, 302)
        location = urlparse(response.headers["Location"])
        self.assertEqual(location.netloc, "meta.wikimedia.org")
        self.assertEqual(location.path, "/w/rest.php/oauth2/authorize")

        params = parse_qs(location.query)
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["client_id"], [app.WIKIMEDIA_OAUTH_CLIENT_ID])
        self.assertIn("state", params)

        with self.client.session_transaction() as sess:
            self.assertEqual(sess["oauth_state"], params["state"][0])
            self.assertEqual(sess["oauth_next"], "/use")

    def test_public_client_uses_pkce_s256(self):
        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", ""), \
                patch.object(app, "WIKIMEDIA_OAUTH_USE_PKCE", True):
            response = self.client.get("/oauth/login")

        params = parse_qs(urlparse(response.headers["Location"]).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertIn("code_challenge", params)

        with self.client.session_transaction() as sess:
            verifier = sess["oauth_code_verifier"]
        self.assertGreaterEqual(len(verifier), 43)
        # The challenge in the URL is derived from the stored verifier.
        self.assertEqual(params["code_challenge"][0], auth_pkce_challenge(verifier))

    def test_confidential_client_skips_pkce(self):
        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", "shh"):
            response = self.client.get("/oauth/login")

        params = parse_qs(urlparse(response.headers["Location"]).query)
        self.assertNotIn("code_challenge", params)
        self.assertNotIn("code_challenge_method", params)

    def test_signed_in_user_is_sent_home(self):
        with self.client.session_transaction() as sess:
            sess["user"] = {"username": "Josef"}

        response = self.client.get("/oauth/login")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.headers["Location"]).path, "/")

    def test_missing_configuration_redirects_to_login(self):
        with patch("app.oauth_configured", return_value=False):
            response = self.client.get("/oauth/login")

        location = urlparse(response.headers["Location"])
        self.assertEqual(location.path, "/login")
        self.assertEqual(parse_qs(location.query)["error"], ["not_configured"])


def auth_pkce_challenge(verifier):
    """Independent S256 implementation used to check app.pkce_pair()."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class CallbackRouteTests(unittest.TestCase):
    def test_canonical_callback_path_matches_registration(self):
        with app.app.test_request_context():
            self.assertEqual(app.url_for("oauth_callback"), "/login/callback")

    def test_legacy_callback_path_still_matches_the_same_view(self):
        app.app.config["TESTING"] = True
        client = app.app.test_client()
        response = client.get("/oauth/callback?error=access_denied")

        location = urlparse(response.headers["Location"])
        self.assertEqual(location.path, "/login")
        self.assertEqual(parse_qs(location.query)["error"], ["denied"])


class PkceTests(unittest.TestCase):
    def test_verifier_and_challenge_are_valid(self):
        verifier, challenge = app.pkce_pair()

        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        self.assertNotIn("=", challenge)
        self.assertEqual(challenge, auth_pkce_challenge(verifier))

    def test_pairs_are_unique(self):
        self.assertNotEqual(app.pkce_pair()[0], app.pkce_pair()[0])


class OAuthCallbackTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def _start(self, next_path=None):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "expected-state"
            sess["oauth_code_verifier"] = "verifier-value"
            if next_path:
                sess["oauth_next"] = next_path

    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_successful_callback_stores_user_and_returns_to_next(self, post, get):
        post.return_value = FakeResponse({"access_token": "tok"})
        get.return_value = FakeResponse({
            "sub": 123,
            "username": "Josef",
            "editcount": 42,
            "groups": ["*", "user"],
            "blocked": False,
        })
        self._start("/use")

        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", ""):
            response = self.client.get("/login/callback?code=abc&state=expected-state")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.headers["Location"]).path, "/use")

        with self.client.session_transaction() as sess:
            self.assertEqual(sess["user"]["username"], "Josef")
            self.assertEqual(sess["user"]["id"], 123)
            self.assertNotIn("oauth_state", sess)
            self.assertNotIn("oauth_code_verifier", sess)

        payload = post.call_args.kwargs["data"]
        self.assertEqual(payload["grant_type"], "authorization_code")
        self.assertEqual(payload["code"], "abc")
        self.assertEqual(payload["client_id"], app.WIKIMEDIA_OAUTH_CLIENT_ID)
        self.assertEqual(payload["code_verifier"], "verifier-value")
        self.assertTrue(payload["redirect_uri"].endswith("/login/callback"))
        self.assertIsNone(post.call_args.kwargs["auth"])
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer tok")

    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_confidential_client_sends_basic_auth(self, post, get):
        post.return_value = FakeResponse({"access_token": "tok"})
        get.return_value = FakeResponse({"username": "Josef"})
        self._start()

        with patch.object(app, "WIKIMEDIA_OAUTH_CLIENT_SECRET", "shh"):
            response = self.client.get("/oauth/callback?code=abc&state=expected-state")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(post.call_args.kwargs["auth"], (app.WIKIMEDIA_OAUTH_CLIENT_ID, "shh"))
        self.assertNotIn("client_id", post.call_args.kwargs["data"])
        self.assertNotIn("code_verifier", post.call_args.kwargs["data"])

    @patch("app.requests.post")
    def test_state_mismatch_is_rejected(self, post):
        self._start()

        response = self.client.get("/oauth/callback?code=abc&state=wrong")

        location = urlparse(response.headers["Location"])
        self.assertEqual(location.path, "/login")
        self.assertEqual(parse_qs(location.query)["error"], ["state"])
        post.assert_not_called()

    @patch("app.requests.post")
    def test_missing_state_in_session_is_rejected(self, post):
        response = self.client.get("/oauth/callback?code=abc&state=expected-state")

        location = urlparse(response.headers["Location"])
        self.assertEqual(parse_qs(location.query)["error"], ["state"])
        post.assert_not_called()

    @patch("app.requests.post")
    def test_cancelled_authorization_is_reported(self, post):
        response = self.client.get("/oauth/callback?error=access_denied")

        location = urlparse(response.headers["Location"])
        self.assertEqual(location.path, "/login")
        self.assertEqual(parse_qs(location.query)["error"], ["denied"])
        post.assert_not_called()

    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_token_failure_is_reported(self, post, get):
        post.side_effect = RuntimeError("token endpoint down")
        self._start()

        response = self.client.get("/oauth/callback?code=abc&state=expected-state")

        location = urlparse(response.headers["Location"])
        self.assertEqual(parse_qs(location.query)["error"], ["failed"])
        get.assert_not_called()

    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_profile_without_username_is_rejected(self, post, get):
        post.return_value = FakeResponse({"access_token": "tok"})
        get.return_value = FakeResponse({"sub": 1})
        self._start()

        response = self.client.get("/oauth/callback?code=abc&state=expected-state")

        location = urlparse(response.headers["Location"])
        self.assertEqual(parse_qs(location.query)["error"], ["failed"])
        with self.client.session_transaction() as sess:
            self.assertNotIn("user", sess)


class LogoutTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_logout_clears_session(self):
        with self.client.session_transaction() as sess:
            sess["user"] = {"username": "Josef"}

        response = self.client.post("/logout")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.headers["Location"]).path, "/")
        with self.client.session_transaction() as sess:
            self.assertNotIn("user", sess)

    def test_logout_requires_post(self):
        self.assertEqual(self.client.get("/logout").status_code, 405)


class HeaderTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_anonymous_header_shows_login_link(self):
        response = self.client.get("/")

        self.assertIn(b"Log in", response.data)
        self.assertNotIn(b"Log out", response.data)

    def test_signed_in_header_shows_username_and_logout(self):
        with self.client.session_transaction() as sess:
            sess["user"] = {"username": "Josef"}

        response = self.client.get("/")

        self.assertIn(b"Josef", response.data)
        self.assertIn(b"Log out", response.data)


if __name__ == "__main__":
    unittest.main()
