"""Запуск: `py -m mcp_market` (stdio). Настройка — переменные FSP_MARKET_TOKEN и FSP_MARKET_API_URL."""
from mcp_market.server import mcp

if __name__ == "__main__":
    mcp.run()  # stdio: в stdout идёт только протокол, поэтому никаких print
