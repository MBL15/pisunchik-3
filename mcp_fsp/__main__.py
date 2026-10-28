"""Запуск: `py -m mcp_fsp` (stdio). Настройка — переменные FSP_API_TOKEN и FSP_API_URL."""
from mcp_fsp.server import mcp

if __name__ == "__main__":
    mcp.run()  # stdio: в stdout идёт только протокол, поэтому никаких print
