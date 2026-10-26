"""Parser for competition logs in the FSP-LOG/1 text format → fully ranked protocol.

Format (one event per line, '#' lines are comments):

    #! FSP-LOG/1 competition=ОРБ-ALG-2026-01 mode=icpc tasks=8 penalty=20
    00:00:00 JOIN  FSP-56-25-00008-5
    00:12:44 RUN   FSP-56-25-00008-5 A WA
    00:19:02 RUN   FSP-56-25-00008-5 A OK
    01:40:00 DSQ   FSP-56-25-00010-9 "Нарушение регламента"
    00:00:00 SCORE FSP-56-26-00021-4 31.50          (mode=score: jury points, summed per participant)

ICPC ranking: solved ↓, penalty ↑ (AC minute + `penalty` per rejected attempt before AC; CE not counted),
last accepted time ↑. Score ranking: total ↓. Equal keys share a place (1, 2, 2, 4).
Disqualified participants are kept in the protocol with status DSQ and no place.
`rank_manual` applies the same ranking to totals entered by hand (protocol from approved applications).
"""
import re
from dataclasses import dataclass, field

from shared.fsp_id import is_valid_fsp_id

_T = r"(\d{2}):(\d{2}):(\d{2})"
HEADER_RE = re.compile(r"^#!\s*FSP-LOG/1\b(.*)$")
JOIN_RE = re.compile(rf"^{_T}\s+JOIN\s+(\S+)$")
RUN_RE = re.compile(rf"^{_T}\s+RUN\s+(\S+)\s+([A-Z])\s+(OK|WA|TL|RE|ML|CE)$")
SCORE_RE = re.compile(rf"^{_T}\s+SCORE\s+(\S+)\s+(\d+(?:\.\d+)?)$")
DSQ_RE = re.compile(rf'^{_T}\s+DSQ\s+(\S+)\s+"([^"]*)"$')


@dataclass
class ParsedLog:
    mode: str
    meta: dict
    entries: list[dict]
    warnings: list[str] = field(default_factory=list)
    events: int = 0
    last_time: str = "00:00:00"


def _seconds(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def _hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def _hhmmss(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_log(text: str) -> ParsedLog:
    meta: dict = {"mode": "icpc", "tasks": 8, "penalty": 20}
    participants: dict[str, dict] = {}
    warnings: list[str] = []
    events, last = 0, 0

    def touch(fid: str) -> dict:
        return participants.setdefault(fid, {"tasks": {}, "score": 0.0, "dsq": None})

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if h := HEADER_RE.match(line):
            for pair in h.group(1).split():
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    meta[k] = int(v) if v.isdigit() else v
            continue
        if line.startswith("#"):
            continue
        m = JOIN_RE.match(line) or RUN_RE.match(line) or SCORE_RE.match(line) or DSQ_RE.match(line)
        if not m:
            warnings.append(f"Строка {lineno}: не распознана — «{line[:60]}»")
            continue
        g = m.groups()
        t, fid = _seconds(*g[:3]), g[3]
        if not is_valid_fsp_id(fid):
            warnings.append(f"Строка {lineno}: некорректный FSP ID «{fid}» (контрольная цифра)")
            continue
        events += 1
        last = max(last, t)
        p = touch(fid)
        if m.re is RUN_RE:
            task, verdict = g[4], g[5]
            st = p["tasks"].setdefault(task, {"solved": False, "wrong": 0, "time": None})
            if st["solved"] or verdict == "CE":
                continue
            if verdict == "OK":
                st["solved"], st["time"] = True, t
            else:
                st["wrong"] += 1
        elif m.re is SCORE_RE:
            p["score"] += float(g[4])
        elif m.re is DSQ_RE:
            p["dsq"] = g[4] or "Дисквалификация"

    mode = str(meta.get("mode", "icpc"))
    penalty = int(meta.get("penalty", 20))
    entries = []
    for fid, p in participants.items():
        solved = [s for s in p["tasks"].values() if s["solved"]]
        pen = sum(s["time"] // 60 + penalty * s["wrong"] for s in solved)
        last_ac = max((s["time"] for s in solved), default=0)
        entries.append({
            "fsp_id": fid,
            "status": "DSQ" if p["dsq"] else "OK",
            "dsq_reason": p["dsq"],
            "solved": len(solved),
            "penalty": pen,
            "score": round(p["score"], 2),
            "tasks": {k: {"solved": v["solved"], "wrong": v["wrong"],
                          "time": _hhmm(v["time"]) if v["time"] is not None else None}
                      for k, v in sorted(p["tasks"].items())},
            "_key": (-len(solved), pen, last_ac) if mode == "icpc" else (-round(p["score"], 2),),
        })
    return ParsedLog(mode=mode, meta=meta, entries=rank_entries(entries, mode), warnings=warnings, events=events,
                     last_time=_hhmmss(last))


def rank_entries(entries: list[dict], mode: str) -> list[dict]:
    """Order entries by their `_key` and assign shared places; DSQ go last without a place. Consumes `_key`."""
    ranked = sorted((e for e in entries if e["status"] == "OK"), key=lambda e: e["_key"])
    prev_key, place = None, 0
    for i, e in enumerate(ranked, start=1):
        tie_key = e["_key"][:2] if mode == "icpc" else e["_key"]
        if tie_key != prev_key:
            place, prev_key = i, tie_key
        e["place"] = place
    dsq = sorted((e for e in entries if e["status"] == "DSQ"), key=lambda e: e["fsp_id"])
    for e in dsq:
        e["place"] = None
    result = ranked + dsq
    for e in result:
        del e["_key"]
    return result


def rank_manual(results: list[dict], mode: str) -> list[dict]:
    """Results entered by the federation (totals, no per-task log) → entries in the same shape as `parse_log`.

    Each result: {fsp_id, solved, penalty, score, dsq, dsq_reason}. ICPC ties share a place on (solved, penalty).
    """
    entries = []
    for r in results:
        solved, penalty, score = int(r.get("solved") or 0), int(r.get("penalty") or 0), round(float(r.get("score") or 0), 2)
        dsq = bool(r.get("dsq"))
        entries.append({
            "fsp_id": r["fsp_id"], "status": "DSQ" if dsq else "OK",
            "dsq_reason": (r.get("dsq_reason") or "Дисквалификация") if dsq else None,
            "solved": solved, "penalty": penalty, "score": score, "tasks": {},
            "_key": (-solved, penalty, 0) if mode == "icpc" else (-score,),
        })
    return rank_entries(entries, mode)
