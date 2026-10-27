"""Telegram через прокси: клиент Bot API строится по `FSP_TELEGRAM_PROXY`, учётные данные прокси не попадают в журнал."""
import asyncio
import unittest
from unittest import mock

import httpx

from gateway_service import notify


def build(proxy: str) -> tuple[httpx.AsyncClient, list[str]]:
    async def make():
        client = notify.make_client()
        await client.aclose()
        return client

    with mock.patch.object(notify.config, "TELEGRAM_PROXY", proxy), self_logs() as logs:
        return asyncio.run(make()), logs


class self_logs:
    def __enter__(self):
        self.records: list[str] = []
        outer = self

        class H(notify.logging.Handler):
            def emit(self, record):
                outer.records.append(record.getMessage())

        self.h = H()
        notify.log.addHandler(self.h)
        self.level = notify.log.level
        notify.log.setLevel(notify.logging.INFO)
        return self.records

    def __exit__(self, *exc):
        notify.log.removeHandler(self.h)
        notify.log.setLevel(self.level)


class ProxyClientTest(unittest.TestCase):
    def test_no_proxy_by_default(self):
        client, logs = build("")
        self.assertIsInstance(client, httpx.AsyncClient)
        self.assertEqual(logs, [])

    def test_socks5_proxy_is_accepted_and_logged_without_credentials(self):
        client, logs = build("socks5://user:s3cret@host.docker.internal:1080")
        self.assertIsInstance(client, httpx.AsyncClient)
        self.assertEqual(len(logs), 1)
        self.assertIn("host.docker.internal:1080", logs[0])
        self.assertNotIn("s3cret", logs[0])
        self.assertNotIn("user", logs[0])

    def test_http_proxy_and_blank_value(self):
        self.assertIsInstance(build("http://proxy.local:3128")[0], httpx.AsyncClient)
        self.assertEqual(build("   ")[1], [])


if __name__ == "__main__":
    unittest.main()


class PollingSurvivesFailuresTest(unittest.TestCase):
    def test_call_swallows_any_transport_error(self):
        class Boom:
            async def post(self, *a, **kw):
                raise RuntimeError("socks protocol error")

        with mock.patch.object(notify, "_http", Boom()):
            self.assertIsNone(asyncio.run(notify._call("getMe")))

    def test_poll_loop_keeps_going_after_errors_and_retries_webhook_reset(self):
        calls: list[str] = []
        script = iter([None, None,                      # deleteWebhook и getUpdates недоступны (туннель не поднят)
                       RuntimeError("boom"),            # неожиданный сбой
                       True, [{"update_id": 7, "message": {"chat": {"id": 1}, "text": "/stop"}}],  # Telegram вернулся
                       asyncio.CancelledError()])       # завершение теста

        async def fake_call(method, **params):
            calls.append(method)
            item = next(script)
            if isinstance(item, BaseException):
                raise item
            return item

        handled = []

        async def fake_handle(message):
            handled.append(message["text"])

        async def no_sleep(_):
            return None

        with mock.patch.object(notify, "_call", fake_call), mock.patch.object(notify, "_handle", fake_handle), \
                mock.patch.object(notify.asyncio, "sleep", no_sleep), self.assertRaises(asyncio.CancelledError):
            asyncio.run(notify.poll_updates())
        self.assertEqual(handled, ["/stop"])
        self.assertEqual(calls.count("deleteWebhook"), 3)  # повторялся, пока не удалось, и больше не вызывался
        self.assertEqual(calls[-1], "getUpdates")
