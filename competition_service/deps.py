"""Клиенты соседних сервисов, общие для `main.py` и подключаемых модулей (`awards`, `appeals`, `integrations`); заполняются в lifespan."""
from shared.http import ServiceClient

clients: dict[str, ServiceClient] = {}
