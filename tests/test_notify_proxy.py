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
