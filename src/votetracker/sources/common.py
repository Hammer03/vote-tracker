"""Helpers shared by the vote parsers."""

from __future__ import annotations

from xml.etree.ElementTree import Element

# How a recorded position is normalized. The raw text is always kept
# alongside, so nothing from the official record is lost.
POSITION_MAP = {
    "yea": "yea",
    "aye": "yea",
    "yes": "yea",
    "guilty": "yea",  # impeachment trials: a vote to convict
    "nay": "nay",
    "no": "nay",
    "not guilty": "nay",
    "present": "present",
    "abstain": "present",
    "not voting": "not_voting",
    "absent": "not_voting",
    "excused": "excused",
}

# 'excused' is kept apart from 'not_voting': many state legislatures record
# an excused absence differently, and we report what the record says.
POSITIONS = ("yea", "nay", "present", "not_voting", "excused", "other")


def normalize_position(raw: str | None) -> str:
    """Map an official vote string to yea / nay / present / not_voting / excused / other.

    'other' covers votes for a person (such as electing the Speaker) and
    anything a legislature records that does not fit the categories above.
    """
    if raw is None:
        return "other"
    return POSITION_MAP.get(raw.strip().lower(), "other")


def text(el: Element | None, path: str | None = None) -> str | None:
    if el is None:
        return None
    node = el.find(path) if path else el
    if node is None or node.text is None:
        return None
    value = " ".join(node.text.split())
    return value or None


def to_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def tally(positions: list[dict]) -> dict:
    counts = {k: 0 for k in POSITIONS}
    for p in positions:
        counts[p["vote"]] = counts.get(p["vote"], 0) + 1
    return counts


def party_tallies(positions: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for p in positions:
        party = p.get("party") or "?"
        bucket = out.setdefault(party, {k: 0 for k in POSITIONS})
        bucket[p["vote"]] += 1
    return out
