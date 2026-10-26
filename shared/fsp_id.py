"""Regional FSP ID format: FSP-56-YY-NNNNN-C (region, registration year, sequence, Luhn check digit).

Formatting/validation is shared because every service must reject malformed IDs (e.g. the
protocol log parser); *issuing* IDs is owned exclusively by sportsman_service.
"""
import re

from shared.domain import REGION_CODE

FSP_ID_RE = re.compile(r"^FSP-(\d{2})-(\d{2})-(\d{5})-(\d)$")


def luhn_check_digit(payload: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def make_fsp_id(year: int, sequence: int, region: str = REGION_CODE) -> str:
    yy = f"{year % 100:02d}"
    seq = f"{sequence:05d}"
    return f"FSP-{region}-{yy}-{seq}-{luhn_check_digit(region + yy + seq)}"


def is_valid_fsp_id(value: str) -> bool:
    m = FSP_ID_RE.match(value or "")
    if not m:
        return False
    region, yy, seq, check = m.groups()
    return luhn_check_digit(region + yy + seq) == int(check)
