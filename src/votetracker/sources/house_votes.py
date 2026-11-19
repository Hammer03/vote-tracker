"""U.S. House roll call votes from the Office of the Clerk.

Each vote is one XML file: https://clerk.house.gov/evs/{year}/roll{NNN}.xml
Member IDs in the file are Bioguide IDs.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from defusedxml import ElementTree as ET

from votetracker.sources.common import normalize_position, party_tallies, tally, text, to_int
from votetracker.util import EASTERN, parse_bill_ref

log = logging.getLogger(__name__)

SOURCE_NAME = "Office of the Clerk, U.S. House of Representatives"
PARTY_NAMES = {"republican": "R", "democratic": "D", "democrat": "D", "independent": "I"}


def xml_url(year: int, number: int) -> str:
    return f"https://clerk.house.gov/evs/{year}/roll{number:03d}.xml"


def page_url(year: int, number: int) -> str:
    return f"https://clerk.house.gov/Votes/{year}{number:03d}"


def _required(vote_type: str | None) -> str | None:
    t = (vote_type or "").upper()
    if "2/3" in t:
        return "2/3"
    if "3/5" in t:
        return "3/5"
    if "QUORUM" in t:
        return None
    return "1/2"


def _session(raw: str | None) -> int | None:
    return to_int(raw)


def parse(xml_bytes: bytes, year: int) -> dict:
    root = ET.fromstring(xml_bytes)
    meta = root.find("vote-metadata")
    if meta is None:
        raise ValueError("not a House roll call file (no vote-metadata)")

    congress = to_int(text(meta, "congress"))
    session = _session(text(meta, "session"))
    number = to_int(text(meta, "rollcall-num"))

    date_raw = text(meta, "action-date")  # e.g. 9-Apr-2025
    time_el = meta.find("action-time")
    time_24 = time_el.get("time-etz") if time_el is not None else None
    vote_date = datetime.strptime(date_raw, "%d-%b-%Y").date() if date_raw else None
    when = None
    if vote_date:
        hh, mm = (int(x) for x in (time_24 or "00:00").split(":"))
        when = datetime(vote_date.year, vote_date.month, vote_date.day, hh, mm, tzinfo=EASTERN)

    legis = text(meta, "legis-num")
    bill = parse_bill_ref(legis, congress) if congress else None

    amendment = None
    amdt_num = text(meta, "amendment-num")
    if amdt_num:
        amendment = {"number": amdt_num, "author": text(meta, "amendment-author")}

    positions = []
    for rv in root.iter("recorded-vote"):
        leg = rv.find("legislator")
        raw_vote = text(rv, "vote")
        if leg is None:
            continue
        positions.append(
            {
                "bioguide": leg.get("name-id") or None,
                "name": " ".join((leg.text or "").split()) or leg.get("unaccented-name"),
                "party": leg.get("party"),
                "state": leg.get("state"),
                "vote": normalize_position(raw_vote),
                "raw": raw_vote,
            }
        )

    official_party_totals = {}
    for tp in root.iter("totals-by-party"):
        name = (text(tp, "party") or "").lower()
        code = PARTY_NAMES.get(name, name[:1].upper() or "?")
        official_party_totals[code] = {
            "yea": to_int(text(tp, "yea-total")) or 0,
            "nay": to_int(text(tp, "nay-total")) or 0,
            "present": to_int(text(tp, "present-total")) or 0,
            "not_voting": to_int(text(tp, "not-voting-total")) or 0,
        }

    vote_type = text(meta, "vote-type")
    return {
        "id": f"house-{congress}-{session}-{number}",
        "chamber": "house",
        "congress": congress,
        "session": session,
        "year": year,
        "number": number,
        "date": vote_date.isoformat() if vote_date else None,
        "datetime": when.isoformat() if when else None,
        "question": text(meta, "vote-question"),
        "description": text(meta, "vote-desc"),
        "vote_type": vote_type,
        "required": _required(vote_type),
        "result": text(meta, "vote-result"),
        "legis_num": legis,
        "bill": bill,
        "amendment": amendment,
        "nomination": None,
        "totals": tally(positions),
        "party_totals": party_tallies(positions),
        "official_party_totals": official_party_totals,
        "positions": positions,
        "source": {"name": SOURCE_NAME, "url": page_url(year, number), "data_url": xml_url(year, number)},
    }


_XML_START = re.compile(rb"^\s*(<\?xml|<rollcall-vote)")


def fetch_new(http, year: int, start: int, max_new: int | None = None) -> list[dict]:
    """Download votes numbered ``start`` and up until the Clerk has no more.

    The Clerk returns 404 (or a non-XML page) past the last vote, which is how
    we know to stop. Cheap when nothing is new: one request.
    """
    found: list[dict] = []
    number = max(1, start)
    misses = 0
    while True:
        if max_new is not None and len(found) >= max_new:
            break
        resp = http.get(xml_url(year, number))
        if not resp.ok or not _XML_START.match(resp.content[:200]):
            # Allow one gap in numbering, then stop.
            misses += 1
            if misses >= 2:
                break
            number += 1
            continue
        misses = 0
        try:
            found.append(parse(resp.content, year))
        except Exception as exc:  # keep going; log the bad file
            log.warning("could not parse House roll %s/%s: %s", year, number, exc)
        number += 1
    log.info("House %s: %d new or refreshed votes", year, len(found))
    return found
