"""Postgres-персистентность для сервисов. Включается переменной FSP_DATABASE_URL; без неё сервисы работают in-memory.

Каждая сущность хранится как строка (key, data JSONB [, индексируемые колонки]). Сервис отдаёт снимок своего состояния,
а здесь считается diff по хэшу: в БД уходят только изменённые строки, исчезнувшие удаляются.
"""
import hashlib
import json
import logging

import psycopg
from psycopg.types.json import Jsonb

from shared import config

log = logging.getLogger("fsp.db")

# table -> key -> (data, extra columns)
Snapshot = dict[str, dict[str, tuple[dict, dict]]]


def _digest(data: dict, extras: dict) -> str:
    return hashlib.sha1(json.dumps([data, extras], sort_keys=True, default=str).encode()).hexdigest()


class Persistence:
    def __init__(self, ddl: str, url: str | None = None) -> None:
        self.url = url if url is not None else config.DATABASE_URL
        self.ddl = ddl
        self._seen: dict[str, dict[str, str]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url)

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(self.ddl)

    def load(self) -> dict[str, dict[str, dict]]:
        """Все строки по таблицам; запоминает хэши, чтобы следующий save не переписывал неизменённое."""
        tables = [t for t in self._tables()]
        out: dict[str, dict[str, dict]] = {}
        with self._connect() as conn:
            for table in tables:
                rows = conn.execute(f"SELECT key, data FROM {table}").fetchall()
                out[table] = {k: d for k, d in rows}
        return out

    def _tables(self) -> list[str]:
        return [line.split()[5] for line in self.ddl.splitlines() if line.strip().upper().startswith("CREATE TABLE IF NOT EXISTS")]

    def mark_loaded(self, snapshot: Snapshot) -> None:
        self._seen = {t: {k: _digest(d, e) for k, (d, e) in rows.items()} for t, rows in snapshot.items()}

    def save(self, snapshot: Snapshot) -> int:
        """Записывает изменения относительно последнего load/save; возвращает число затронутых строк."""
        changed = 0
        with self._connect() as conn:
            for table, rows in snapshot.items():
                seen = self._seen.setdefault(table, {})
                for key, (data, extras) in rows.items():
                    digest = _digest(data, extras)
                    if seen.get(key) == digest:
                        continue
                    cols = ["key", "data", *extras]
                    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                        f"ON CONFLICT (key) DO UPDATE SET {sets}",
                        [key, Jsonb(data), *extras.values()])
                    seen[key] = digest
                    changed += 1
                for key in set(seen) - set(rows):
                    conn.execute(f"DELETE FROM {table} WHERE key = %s", [key])
                    del seen[key]
                    changed += 1
        return changed
