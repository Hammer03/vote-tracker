"""Per-member vote statistics, computed only from official roll call records.

* Missed votes: roll calls where the member is recorded as "Not Voting" or
  "Absent", out of all roll calls where the member appears. Excused absences
  are counted separately and are not included in "missed".
* Votes with party: of the roll calls where the member voted Yea or Nay and
  their party's members did not split evenly, how often the member voted
  the same way as most of their party. Independents are compared with the
  party they caucus with, when the member data records one.

These are plain counts. No labels or judgments are attached. Each result
also says which dates it covers, so partial records are never presented as
a full career.
"""

from __future__ import annotations

from collections import defaultdict

from votetracker.util import pct


def _party_majorities(vote: dict, party_of) -> dict[str, str | None]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"yea": 0, "nay": 0})
    for p in vote["positions"]:
        if p["vote"] in ("yea", "nay"):
            party = party_of(p)
            if party:
                counts[party][p["vote"]] += 1
    out = {}
    for party, c in counts.items():
        if c["yea"] > c["nay"]:
            out[party] = "yea"
        elif c["nay"] > c["yea"]:
            out[party] = "nay"
        else:
            out[party] = None
    return out


def compute(votes: list[dict], caucus_code, id_key: str = "bioguide") -> dict[str, dict[str, dict]]:
    """Returns {member_id: {chamber: stats}}."""

    def party_of(p: dict) -> str | None:
        return caucus_code(p.get(id_key)) or p.get("party")

    acc: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"total": 0, "cast": 0, "missed": 0, "excused": 0, "present": 0,
                 "party_eligible": 0, "party_same": 0, "first": None, "last": None}
    )
    for vote in votes:
        majorities = _party_majorities(vote, party_of)
        day = vote.get("date")
        for p in vote["positions"]:
            mid = p.get(id_key)
            if not mid:
                continue
            a = acc[(mid, vote["chamber"])]
            a["total"] += 1
            if day:
                a["first"] = day if not a["first"] or day < a["first"] else a["first"]
                a["last"] = day if not a["last"] or day > a["last"] else a["last"]
            if p["vote"] == "not_voting":
                a["missed"] += 1
            elif p["vote"] == "excused":
                a["excused"] += 1
            else:
                a["cast"] += 1
            if p["vote"] == "present":
                a["present"] += 1
            if p["vote"] in ("yea", "nay"):
                maj = majorities.get(party_of(p) or "")
                if maj:
                    a["party_eligible"] += 1
                    if maj == p["vote"]:
                        a["party_same"] += 1

    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for (mid, chamber), a in acc.items():
        out[mid][chamber] = {
            "roll_calls": a["total"],
            "votes_cast": a["cast"],
            "missed": a["missed"],
            "missed_pct": pct(a["missed"], a["total"]),
            "excused": a["excused"],
            "present": a["present"],
            "with_party": a["party_same"],
            "with_party_eligible": a["party_eligible"],
            "with_party_pct": pct(a["party_same"], a["party_eligible"]),
            "covers_from": a["first"],
            "covers_to": a["last"],
        }
    return dict(out)
