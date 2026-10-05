import importlib
import os
import unittest
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient


TEST_KEY = "fake-sync-key-for-tests-only"
FAKE_TOKEN = "fake-bearer-token-for-tests-only"
NOW = 1_800_000_000
ROUTES = ("/workout/123", "/master-exercises/456")


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import with a fake environment key, restoring the environment afterward.
        with patch.dict(os.environ, {"NEXT_SET_SYNC_KEY": TEST_KEY}):
            cls.module = importlib.import_module("adapters.biolayne.app")

    def setUp(self):
        self.enterContext(patch.object(self.module, "SYNC_KEY", TEST_KEY))
        self.enterContext(patch.object(self.module, "_token", None))
        self.enterContext(patch.object(self.module, "_expires", 0))
        self.enterContext(patch.object(self.module.time, "time", return_value=NOW))
        # Fail closed: an unmocked requests call must never reach the network.
        self.network = self.enterContext(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Real upstream HTTP is forbidden in tests"),
        ))
        self.upstream = self.enterContext(patch.object(self.module.requests, "get"))
        self.upstream.side_effect = AssertionError("Unexpected upstream request")
        self.client = self.enterContext(TestClient(self.module.app))
        self.headers = {"X-Next-Set-Key": TEST_KEY}

    def tearDown(self):
        self.network.assert_not_called()

    def store_token(self, remaining=3600):
        response = self.client.post("/token", headers=self.headers, json={
            "token": FAKE_TOKEN, "expires": NOW + remaining,
        })
        self.assertEqual(response.status_code, 200)
        return response

    def mock_response(self, status=200, data=None):
        response = Mock(spec=requests.Response)
        response.status_code = status
        response.json.return_value = data if data is not None else [{"id": 1}]
        self.upstream.side_effect = None
        self.upstream.return_value = response
        return response

    def assert_sanitized(self, response):
        for forbidden in (TEST_KEY, FAKE_TOKEN, "Authorization", "Traceback", "sensitive-upstream"):
            self.assertNotIn(forbidden, response.text)

    def test_health_public(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "ok": True, "has_token": False,
            "token_expires": None, "seconds_remaining": None,
        })

    def test_token_missing_key(self):
        response = self.client.post("/token", json={"token": FAKE_TOKEN, "expires": NOW + 3600})
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.module._token)

    def test_token_wrong_key(self):
        response = self.client.post("/token", headers={"X-Next-Set-Key": "wrong"},
                                    json={"token": FAKE_TOKEN, "expires": NOW + 3600})
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.module._token)

    def test_token_correct_key(self):
        response = self.store_token()
        self.assertEqual(response.json(), {"ok": True, "expires": NOW + 3600})
        self.assertEqual(self.module._token, FAKE_TOKEN)
        self.assert_sanitized(response)

    def test_health_never_returns_token(self):
        self.store_token()
        response = self.client.get("/health")
        self.assertTrue(response.json()["has_token"])
        self.assertEqual(response.json()["seconds_remaining"], 3600)
        self.assert_sanitized(response)

    def test_proxy_auth_required(self):
        for route in ROUTES:
            for headers in ({}, {"X-Next-Set-Key": "wrong"}):
                with self.subTest(route=route, headers_present=bool(headers)):
                    self.assertEqual(self.client.get(route, headers=headers).status_code, 401)
        self.upstream.assert_not_called()

    def test_no_bearer_token(self):
        for route in ROUTES:
            self.assertEqual(self.client.get(route, headers=self.headers).status_code, 401)
        self.upstream.assert_not_called()

    def test_expired_token(self):
        self.store_token(-1)
        for route in ROUTES:
            self.assertEqual(self.client.get(route, headers=self.headers).status_code, 401)
        self.upstream.assert_not_called()

    def test_expiring_within_thirty_seconds(self):
        for remaining in (0, 1, 29, 30):
            self.store_token(remaining)
            for route in ROUTES:
                with self.subTest(remaining=remaining, route=route):
                    self.assertEqual(self.client.get(route, headers=self.headers).status_code, 401)
        self.upstream.assert_not_called()

    def test_valid_token_both_endpoints(self):
        self.store_token(31)
        for route, path, params in (
            (ROUTES[0], "workout-exercises", {"workoutId": 123}),
            (ROUTES[1], "master-exercises", {"masterWorkoutId": 456}),
        ):
            with self.subTest(route=route):
                upstream = self.mock_response()
                response = self.client.get(route, headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [{"id": 1}])
                self.upstream.assert_called_with(
                    f"{self.module.API_BASE}/{path}", params=params,
                    headers={"Accept": "application/json", "Authorization": f"Bearer {FAKE_TOKEN}",
                             "Origin": "https://biolayne.com", "Referer": "https://biolayne.com/"},
                    timeout=30, allow_redirects=False,
                )
                upstream.close.assert_called_once()

    def test_upstream_401(self):
        self.store_token()
        for route in ROUTES:
            upstream = self.mock_response(401)
            response = self.client.get(route, headers=self.headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "BioLayne rejected the bearer token."})
            upstream.json.assert_not_called()
            upstream.close.assert_called_once()

    def check_request_error(self, exception, status):
        self.store_token()
        self.upstream.side_effect = exception(f"sensitive-upstream {FAKE_TOKEN} {TEST_KEY}")
        for route in ROUTES:
            with self.subTest(route=route):
                response = self.client.get(route, headers=self.headers)
                self.assertEqual(response.status_code, status)
                self.assert_sanitized(response)

    def test_timeout(self):
        self.check_request_error(requests.Timeout, 504)

    def test_connection_error(self):
        self.check_request_error(requests.ConnectionError, 502)

    def test_other_request_error(self):
        self.check_request_error(requests.RequestException, 502)

    def test_non_success_status(self):
        self.store_token()
        for status in (301, 302, 400, 403, 404, 429, 500, 503):
            for route in ROUTES:
                with self.subTest(status=status, route=route):
                    upstream = self.mock_response(status)
                    upstream.text = f"sensitive-upstream {FAKE_TOKEN}"
                    response = self.client.get(route, headers=self.headers)
                    self.assertEqual(response.status_code, 502)
                    self.assert_sanitized(response)
                    upstream.json.assert_not_called()
                    upstream.close.assert_called_once()

    def test_malformed_json(self):
        self.store_token()
        for route in ROUTES:
            upstream = self.mock_response()
            upstream.json.side_effect = requests.exceptions.JSONDecodeError(
                "sensitive-upstream", FAKE_TOKEN, 0,
            )
            response = self.client.get(route, headers=self.headers)
            self.assertEqual(response.status_code, 502)
            self.assert_sanitized(response)
            upstream.close.assert_called_once()

    def test_validation_does_not_echo_submitted_credentials(self):
        for payload in (
            {"token": FAKE_TOKEN},
            {"token": {"credential": FAKE_TOKEN}, "expires": NOW + 3600},
            {"token": FAKE_TOKEN, "expires": FAKE_TOKEN},
        ):
            response = self.client.post("/token", headers=self.headers, json=payload)
            self.assertEqual(response.status_code, 422)
            self.assert_sanitized(response)

    def test_docs_and_schema_no_credentials(self):
        self.store_token()
        for path in ("/docs", "/openapi.json"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assert_sanitized(response)
        self.assertFalse(self.module.app.debug)

    def test_non_ascii_wrong_key_is_unauthorized(self):
        # ASGI headers decode as latin-1; bytes allow exercising non-ASCII input.
        response = self.client.get(ROUTES[0], headers={b"X-Next-Set-Key": b"wrong-\xff"})
        self.assertEqual(response.status_code, 401)
        self.upstream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
