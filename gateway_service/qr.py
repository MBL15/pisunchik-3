import io

import segno


def qr_svg(data: str, dark: str = "#0f172a", light: str = "#ffffff", scale: int = 4) -> str:
    """Inline SVG QR code (error correction M) — scannable, no external requests."""
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=scale, border=2, dark=dark, light=light,
                                    xmldecl=False, svgns=True, svgclass="qr", lineclass=None)
    return buf.getvalue().decode()
