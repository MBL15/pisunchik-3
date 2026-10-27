"""Второй раунд ревью: защита от подбора пароля, секрет подписи, время проверки пароля."""
import importlib
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from gateway_service import throttle
from shared import config
from sportsman_service import main as sportsman


class LoginThrottleTest(unittest.TestCase):
    def setUp(self):
        throttle._fails.clear()

    def test_blocks_after_the_limit_and_releases_after_the_window(self):
        k = throttle.key("1.2.3.4", " Admin@FSP56.ru ")
        self.assertEqual(k, throttle.key("1.2.3.4", "admin@fsp56.ru"))  # регистр и пробелы лимит не обходят
        for i in range(throttle.LIMIT):
            self.assertEqual(throttle.retry_after(k, now=100 + i), 0)
            throttle.failed(k, now=100 + i)
        self.assertGreater(throttle.retry_after(k, now=110), 0)
        self.assertEqual(throttle.retry_after(k, now=100 + throttle.WINDOW + 1), 0)

    def test_success_resets_and_other_addresses_are_independent(self):
        k = throttle.key("1.2.3.4", "a@b.ru")
        for _ in range(throttle.LIMIT):
            throttle.failed(k, now=1)
        self.assertEqual(throttle.retry_after(throttle.key("5.6.7.8", "a@b.ru"), now=2), 0)
        throttle.succeeded(k)
        self.assertEqual(throttle.retry_after(k, now=2), 0)


class SecretKeyTest(unittest.TestCase):
    def reload(self, **env):
        with mock.patch.dict(os.environ, env, clear=False):
            return importlib.reload(config)

    def tearDown(self):
        importlib.reload(config)

    def test_dev_secret_is_refused_for_a_public_address(self):
        with mock.patch.dict(os.environ, {"FSP_PUBLIC_BASE_URL": "https://fsp.example.ru"}):
            os.environ.pop("FSP_SECRET_KEY", None)
            with self.assertRaisesRegex(RuntimeError, "FSP_SECRET_KEY"):
                importlib.reload(config)

    def test_dev_secret_is_fine_on_localhost_and_a_custom_one_is_fine_anywhere(self):
        self.reload(FSP_PUBLIC_BASE_URL="http://localhost:8000")
        self.reload(FSP_PUBLIC_BASE_URL="https://fsp.example.ru", FSP_SECRET_KEY="x" * 40)


class VerifyTimingTest(unittest.TestCase):
    def test_unknown_email_still_costs_a_password_hash_run(self):
        sportsman.store.__init__()
        with TestClient(sportsman.app) as api:
            with mock.patch.object(sportsman, "verify_password", wraps=sportsman.verify_password) as spy:
                r = api.post("/api/v1/auth/verify", json={"email": "nobody@example.ru", "password": "whatever-123"})
                self.assertEqual(r.status_code, 401)
                self.assertEqual(spy.call_count, 1)
                ok = api.post("/api/v1/auth/verify", json={"email": "admin@fsp56.ru", "password": "fsp2026"})
                self.assertEqual(ok.status_code, 200)


if __name__ == "__main__":
    unittest.main()
