"""U.S. Senate roll call votes from the Senate's Legislative Information System.

Vote list: https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml
One vote:  https://www.senate.gov/legislative/LIS/roll_call_votes/vote{c}{s}/vote_{c}_{s}_{NNNNN}.xml
Member IDs in the file are Senate LIS IDs; we map them to Bioguide IDs.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from defusedxml import ElementTree as ET

from votetracker.sources.common import normalize_position, party_tallies, tally, text, to_int
from votetracker.util import EASTERN, make_bill_ref, normalize_bill_type, ordinal

log = logging.getLogger(__name__)

SOURCE_NAME = "U.S. Senate, Legislative Information System"


def menu_url(congress: int, session: int) -> str:
    return f"https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"


def xml_url(congress: int, session: int, number: int) -> str:
    return (
        f"https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/"
        f"vote_{congress}_{session}_{number:05d}.xml"
    )


def page_url(congress: int, session: int, number: int) -> str:
    return xml_url(congress, session, number)[: -len(".xml")] + ".htm"


def parse_menu(xml_bytes: bytes) -> list[int]:
    root = ET.fromstring(xml_bytes)
    numbers = []
    for v in root.iter("vote"):
        n = to_int(text(v, "vote_number"))
        if n:
            numbers.append(n)
    return sorted(set(numbers))


_DATE_RE = re.compile(r"([A-Za-z]+ \d{1,2}, \d{4}),?\s+(\d{1,2}:\d{2}\s*[AP]M)")
_DATE_ONLY_RE = re.compile(r"([A-Za-z]+ \d{1,2}, \d{4})")


def _parse_when(raw: str | None) -> datetime | None:
    """'May 18, 2026,  05:36 PM' -> aware datetime in Eastern time."""
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    try:
        if m:
            stamp = f"{m.group(1)} {m.group(2).replace(' ', '')}"
            return datetime.strptime(stamp, "%B %d, %Y %I:%M%p").replace(tzinfo=EASTERN)
        d = _DATE_ONLY_RE.search(raw)
        if d:
            return datetime.strptime(d.group(1), "%B %d, %Y").replace(tzinfo=EASTERN)
    except ValueError:
        return None
    return None


def parse(xml_bytes: bytes, lis_to_bioguide: dict[str, str]) -> dict:
    root = ET.fromstring(xml_bytes)
    if root.tag != "roll_call_vote":
        raise ValueError("not a Senate roll call file")

    congress = to_int(text(root, "congress"))
    session = to_int(text(root, "session"))
    number = to_int(text(root, "vote_number"))
    when = _parse_when(text(root, "vote_date"))

    doc = root.find("document")
    bill = None
    nomination = None
    if doc is not None:
        dtype_raw = text(doc, "document_type") or ""
        dnum_raw = text(doc, "document_number")
        dcongress = to_int(text(doc, "document_congress")) or congress
        btype = normalize_bill_type(dtype_raw)
        if btype and dnum_raw and to_int(dnum_raw):
            bill = make_bill_ref(dcongress, btype, to_int(dnum_raw))
        elif dtype_raw.upper().startswith("PN") and dnum_raw:
            nomination = {
                "id": f"PN{dnum_raw}",
                "title": text(doc, "document_title"),
                "url": f"https://www.congress.gov/nomination/{ordinal(dcongress)}-congress/{dnum_raw}",
            }

    amendment = None
    amdt = root.find("amendment")
    if amdt is not None and text(amdt, "amendment_number"):
        amendment = {
            "number": text(amdt, "amendment_number"),
            "to": text(amdt, "amendment_to_document_number"),
            "purpose": text(amdt, "amendment_purpose"),
        }

    positions = []
    unmapped = 0
    for m in root.iter("member"):
        lis = text(m, "lis_member_id")
        bioguide = lis_to_bioguide.get(lis or "")
        if lis and not bioguide:
            unmapped += 1
        raw_vote = text(m, "vote_cast")
        positions.append(
            {
                "bioguide": bioguide,
                "lis": lis,
                "name": " ".join(x for x in (text(m, "first_name"), text(m, "last_name")) if x),
                "party": text(m, "party"),
                "state": text(m, "state"),
                "vote": normalize_position(raw_vote),
                "raw": raw_vote,
            }
        )
    if unmapped:
        log.debug("Senate vote %s-%s-%s: %d members not in current data", congress, session, number, unmapped)

    tie = root.find("tie_breaker")
    tie_breaker = None
    if tie is not None and text(tie, "by_whom"):
        tie_breaker = {"by": text(tie, "by_whom"), "vote": text(tie, "tie_breaker_vote")}

    return {
        "id": f"senate-{congress}-{session}-{number}",
        "chamber": "senate",
        "congress": congress,
        "session": session,
        "year": when.year if when else None,
        "number": number,
        "date": when.date().isoformat() if when else None,
        "datetime": when.isoformat() if when else None,
        "question": text(root, "question") or text(root, "vote_question_text"),
        "question_full": text(root, "vote_question_text"),
        "description": text(root, "vote_title") or text(root, "vote_document_text"),
        "vote_type": None,
        "required": text(root, "majority_requirement"),
        "result": text(root, "vote_result"),
        "result_text": text(root, "vote_result_text"),
        "legis_num": text(doc, "document_name") if doc is not None else None,
        "bill": bill,
        "amendment": amendment,
        "nomination": nomination,
        "tie_breaker": tie_breaker,
        "totals": tally(positions),
        "party_totals": party_tallies(positions),
        "positions": positions,
        "source": {
            "name": SOURCE_NAME,
            "url": page_url(congress, session, number),
            "data_url": xml_url(congress, session, number),
        },
    }


def fetch_new(http, congress: int, session: int, known: set[int], lis_to_bioguide: dict[str, str],
              refetch: set[int] | None = None, max_new: int | None = None) -> list[dict]:
    resp = http.get(menu_url(congress, session))
    if not resp.ok:
        log.info("Senate %s-%s: no vote list yet (HTTP %s)", congress, session, resp.status)
        return []
    try:
        numbers = parse_menu(resp.content)
    except Exception as exc:
        log.warning("Senate vote list unreadable: %s", exc)
        return []
    wanted = [n for n in numbers if n not in known or (refetch and n in refetch)]
    if max_new is not None:
        wanted = wanted[:max_new]
    found = []
    for n in wanted:
        r = http.get(xml_url(congress, session, n))
        if not r.ok:
            log.warning("Senate vote %s-%s-%s: HTTP %s", congress, session, n, r.status)
            continue
        try:
            found.append(parse(r.content, lis_to_bioguide))
        except Exception as exc:
            log.warning("could not parse Senate vote %s-%s-%s: %s", congress, session, n, exc)
    log.info("Senate %s-%s: %d new or refreshed votes", congress, session, len(found))
    return found
