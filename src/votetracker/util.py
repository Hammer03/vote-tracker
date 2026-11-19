"""Small shared helpers: dates, Congress numbers, bill identifiers, parties."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# Bill types as used by Congress.gov (lowercase, no punctuation).
BILL_TYPES = ("hr", "s", "hres", "sres", "hjres", "sjres", "hconres", "sconres")

BILL_LABELS = {
    "hr": "H.R.",
    "s": "S.",
    "hres": "H.Res.",
    "sres": "S.Res.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
}

CONGRESS_GOV_BILL_PATH = {
    "hr": "house-bill",
    "s": "senate-bill",
    "hres": "house-resolution",
    "sres": "senate-resolution",
    "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution",
}

PARTY_CODES = {
    "republican": "R",
    "democrat": "D",
    "democratic": "D",
    "independent": "I",
    "libertarian": "L",
    "green": "G",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def congress_for_year(year: int) -> int:
    """The Congress whose regular sessions fall in this calendar year.

    The 119th Congress covers 2025 and 2026. (Jan 1-3 of odd years
    technically belong to the previous Congress; roll call votes almost never
    happen then, so we ignore that edge.)
    """
    return (year - 1787) // 2


def years_for_congress(congress: int) -> tuple[int, int]:
    first = 1787 + 2 * congress
    return first, first + 1


def session_for_year(congress: int, year: int) -> int:
    first, _ = years_for_congress(congress)
    return 1 if year == first else 2


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def party_code(party: str | None) -> str | None:
    if not party:
        return None
    p = party.strip()
    if len(p) == 1:
        return p.upper()
    return PARTY_CODES.get(p.lower(), p[:1].upper())


_BILL_RE = re.compile(r"^\s*([A-Za-z.\s]+?)\s*(\d+)\s*$")


def normalize_bill_type(raw: str) -> str | None:
    """'H R', 'H.R.', 'H J RES', 'S.Con.Res.' -> 'hr', 'hjres', 'sconres'."""
    t = re.sub(r"[^a-z]", "", raw.lower())
    return t if t in BILL_TYPES else None


def parse_bill_ref(text: str | None, congress: int) -> dict | None:
    """Parse a bill reference like 'H R 1228' or 'S.J.Res. 80'.

    Returns None for things that are not bills (quorum calls, nominations,
    motions to adjourn, etc.).
    """
    if not text:
        return None
    m = _BILL_RE.match(text)
    if not m:
        return None
    btype = normalize_bill_type(m.group(1))
    if not btype:
        return None
    number = int(m.group(2))
    return make_bill_ref(congress, btype, number)


def make_bill_ref(congress: int, btype: str, number: int) -> dict:
    return {
        "id": bill_id(congress, btype, number),
        "congress": congress,
        "type": btype,
        "number": number,
        "label": f"{BILL_LABELS[btype]} {number}",
        "url": congress_gov_bill_url(congress, btype, number),
    }


def bill_id(congress: int, btype: str, number: int) -> str:
    return f"{congress}-{btype}-{number}"


def congress_gov_bill_url(congress: int, btype: str, number: int) -> str:
    return f"https://www.congress.gov/bill/{ordinal(congress)}-congress/{CONGRESS_GOV_BILL_PATH[btype]}/{number}"


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def html_to_text(raw: str | None) -> str | None:
    """Very small HTML-to-text converter for CRS summaries."""
    if not raw:
        return None
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h\d)\s*/?>", "\n", raw)
    text = re.sub(r"(?i)<\s*li[^>]*>", "- ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip() or None


def years_between(start: str, today: date | None = None) -> float:
    d0 = date.fromisoformat(start)
    d1 = today or date.today()
    return round((d1 - d0).days / 365.25, 1)


def pct(part: int, whole: int) -> float | None:
    if not whole:
        return None
    return round(100.0 * part / whole, 1)
