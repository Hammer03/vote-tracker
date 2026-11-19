"""Senate roll call votes from Voteview (UCLA), used when senate.gov is unreachable.

senate.gov's firewall refuses automated requests from cloud servers, which
includes GitHub Actions. Voteview republishes the official roll call record
for every Congress as plain CSV files, updated within days of new votes:

  https://voteview.com/static/data/out/rollcalls/S{congress}_rollcalls.csv
  https://voteview.com/static/data/out/votes/S{congress}_votes.csv
  https://voteview.com/static/data/out/members/S{congress}_members.csv

Votes keep the Senate's own numbering (clerk_rollnumber within a session), so
records match senate.gov and are replaced by the senate.gov version whenever
that site answers. Voteview's ideology scores are not used.
"""

from __future__ import annotations

import csv
import io
import logging
import re

from votetracker.sources import senate_votes
from votetracker.sources.common import party_tallies, tally
from votetracker.util import make_bill_ref, normalize_bill_type, ordinal

log = logging.getLogger(__name__)

BASE = "https://voteview.com/static/data/out"
SOURCE_NAME = "Voteview (UCLA), compiled from official U.S. Senate records"

# cast_code -> (normalized position, official-style text)
# Paired and announced positions are not recorded votes; the Senate's own
# record lists those senators as not voting, so we do the same and keep
# the announcement in the raw text.
CAST = {
    1: ("yea", "Yea"),
    2: ("not_voting", "Not Voting (paired Yea)"),
    3: ("not_voting", "Not Voting (announced Yea)"),
    4: ("not_voting", "Not Voting (announced Nay)"),
    5: ("not_voting", "Not Voting (paired Nay)"),
    6: ("nay", "Nay"),
    7: ("present", "Present"),
    8: ("present", "Present"),
    9: ("not_voting", "Not Voting"),
}
PARTY = {100: "D", 200: "R", 328: "I"}


def url(kind: str, congress: int) -> str:
    return f"{BASE}/{kind}/S{congress}_{kind}.csv"


def _csv(http, kind: str, congress: int) -> list[dict] | None:
    resp = http.get(url(kind, congress))
    if not resp.ok:
        log.warning("Voteview %s S%s: HTTP %s", kind, congress, resp.status)
        return None
    return list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


_BILL = re.compile(r"^\s*([A-Za-z.\s]+?)\s*(\d+)")


def _refs(bill_number: str | None, congress: int):
    if not bill_number:
        return None, None
    raw = bill_number.strip()
    if raw.upper().startswith("PN"):
        num = raw[2:].strip()
        return None, {"id": f"PN{num}", "title": None,
                      "url": f"https://www.congress.gov/nomination/{ordinal(congress)}-congress/{num.split('-')[0]}"}
    m = _BILL.match(raw)
    if m:
        btype = normalize_bill_type(m.group(1))
        if btype:
            return make_bill_ref(congress, btype, int(m.group(2))), None
    return None, None


def fetch_senate(http, congress: int, lis_to_bioguide: dict | None = None) -> list[dict] | None:
    """All Senate roll calls Voteview has for this Congress, in our vote format."""
    rollcalls = _csv(http, "rollcalls", congress)
    votes = _csv(http, "votes", congress)
    members = _csv(http, "members", congress)
    if rollcalls is None or votes is None or members is None:
        return None

    people = {}
    for m in members:
        icpsr = _int(m.get("icpsr"))
        if icpsr is None:
            continue
        people[icpsr] = {
            "bioguide": (m.get("bioguide_id") or "").strip() or None,
            "name": (m.get("bioname") or "").strip(),
            "party": PARTY.get(_int(m.get("party_code")) or 0),
            "state": (m.get("state_abbrev") or "").strip() or None,
        }

    by_roll: dict[int, list[dict]] = {}
    for v in votes:
        code = _int(v.get("cast_code"))
        if not code:
            continue  # 0 = not a member of the Senate at the time
        pos, raw = CAST.get(code, ("other", str(v.get("cast_code"))))
        person = people.get(_int(v.get("icpsr")) or -1, {})
        by_roll.setdefault(_int(v.get("rollnumber")), []).append({
            "bioguide": person.get("bioguide"),
            "lis": None,
            "name": person.get("name"),
            "party": person.get("party"),
            "state": person.get("state"),
            "vote": pos,
            "raw": raw,
        })

    out = []
    for rc in rollcalls:
        roll = _int(rc.get("rollnumber"))
        session = _int(rc.get("session"))
        number = _int(rc.get("clerk_rollnumber"))
        if roll is None or not session or not number:
            continue
        positions = by_roll.get(roll, [])
        day = (rc.get("date") or "").strip() or None
        bill, nomination = _refs(rc.get("bill_number"), congress)
        question = (rc.get("vote_question") or "").strip() or None
        out.append({
            "id": f"senate-{congress}-{session}-{number}",
            "chamber": "senate",
            "congress": congress,
            "session": session,
            "year": int(day[:4]) if day else None,
            "number": number,
            "date": day,
            "datetime": None,
            "question": question,
            "question_full": question,
            "description": (rc.get("vote_desc") or rc.get("dtl_desc") or "").strip() or None,
            "vote_type": None,
            "required": None,
            "result": (rc.get("vote_result") or "").strip() or None,
            "result_text": None,
            "legis_num": (rc.get("bill_number") or "").strip() or None,
            "bill": bill,
            "amendment": None,
            "nomination": nomination,
            "tie_breaker": None,
            "totals": tally(positions),
            "party_totals": party_tallies(positions),
            "positions": positions,
            "source": {
                "name": SOURCE_NAME,
                "url": senate_votes.page_url(congress, session, number),
                "data_url": f"https://voteview.com/rollcall/RS{congress}{roll:04d}",
                "via": "voteview",
            },
        })
    log.info("Voteview S%s: %d roll calls", congress, len(out))
    return out
