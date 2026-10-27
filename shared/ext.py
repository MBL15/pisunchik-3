"""Именованные таблицы записей с автонумерацией для дополнительных сценариев (апелляции, нормативы, группы, токены интеграций…).

Запись — обычный JSON-словарь с `id`: её проверяет pydantic-модель модуля в момент создания, а в снимок для Postgres
вся группа таблиц уходит одной строкой `*_meta` (см. `snapshot` / `restore` в `store.py` сервиса).
"""
from typing import Any


class ExtTables:
    def __init__(self) -> None:
        self.tables: dict[str, dict[int, dict]] = {}
        self.seq: dict[str, int] = {}

    def table(self, name: str) -> dict[int, dict]:
        return self.tables.setdefault(name, {})

    def add(self, name: str, item: dict) -> dict:
        """Кладёт запись под новым `id` (поле `id` в `item` перезаписывается) и возвращает её."""
        self.seq[name] = self.seq.get(name, 0) + 1
        item["id"] = self.seq[name]
        self.table(name)[item["id"]] = item
        return item

    def dump(self) -> dict[str, Any]:
        return {"tables": {n: {str(k): v for k, v in t.items()} for n, t in self.tables.items()}, "seq": dict(self.seq)}

    def load(self, data: dict[str, Any]) -> None:
        self.tables = {n: {int(k): v for k, v in t.items()} for n, t in data.get("tables", {}).items()}
        self.seq = {n: int(v) for n, v in data.get("seq", {}).items()}
