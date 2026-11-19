"""Per-member vote statistics, computed only from official roll call records.

* Missed votes: roll calls where the member's recorded position is
  "Not Voting", out of all roll calls where the member appears.
* Votes with party: of the roll calls where the member voted Yea or Nay and
  their party's members did not split evenly, how often the member voted
  the same way as most of their party. Independents are compared with the
  party they caucus with, as recorded in the member data.

These are plain counts. No labels or judgments are attached.
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


def compute(votes: list[dict], caucus_code) -> dict[str, dict[str, dict]]:
    """Returns {bioguide: {chamber: stats}}."""

    def party_of(p: dict) -> str | None:
        return caucus_code(p.get("bioguide")) or p.get("party")

    acc: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, "cast": 0, "missed": 0, "present": 0, "party_eligible": 0, "party_same": 0}
    )
    for vote in votes:
        majorities = _party_majorities(vote, party_of)
        for p in vote["positions"]:
            bid = p.get("bioguide")
            if not bid:
                continue
            a = acc[(bid, vote["chamber"])]
            a["total"] += 1
            if p["vote"] == "not_voting":
                a["missed"] += 1
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
    for (bid, chamber), a in acc.items():
        out[bid][chamber] = {
            "roll_calls": a["total"],
            "votes_cast": a["cast"],
            "missed": a["missed"],
            "missed_pct": pct(a["missed"], a["total"]),
            "present": a["present"],
            "with_party": a["party_same"],
            "with_party_eligible": a["party_eligible"],
            "with_party_pct": pct(a["party_same"], a["party_eligible"]),
        }
    return dict(out)
