"""Public files for state legislatures."""

from __future__ import annotations

import re
from collections import defaultdict

from votetracker.builders import stats as stats_b
from votetracker.builders.votes import member_histories
from votetracker.sources.openstates import SOURCE, session_slug

RECENT = 20


def vote_key(v: dict) -> str:
    return f"v1/state-votes/{v['state']}/{session_slug(v['session'])}/{v['uuid']}.json"


def bill_key(code: str, session: str, bill_id: str) -> str:
    return f"v1/state-bills/{code}/{session_slug(session)}/{bill_id}.json"


def card(p: dict) -> dict:
    return {k: p.get(k) for k in ("id", "name", "party", "party_code", "title", "chamber", "state", "district", "image")}


def summary(v: dict) -> dict:
    return {
        "id": v["id"],
        "state": v["state"],
        "session": v["session"],
        "chamber": v["chamber"],
        "chamber_name": v.get("chamber_name"),
        "date": v["date"],
        "question": v["question"],
        "result": v["result"],
        "bill": {"id": v["bill"]["id"], "label": v["bill"]["label"], "title": v["bill"].get("title")},
        "totals": v["totals"],
        "named_votes": v["named_votes"],
        "path": vote_key(v)[len("v1/"):],
    }


def _district_sort(d: str | None):
    d = d or ""
    m = re.match(r"(\d+)", d)
    return (0, int(m.group(1)), d) if m else (1, 0, d)


def build_state(pub, code: str, name: str, info: dict, people: list[dict], votes: list[dict],
                bills: dict[str, dict], last_checked: str | None) -> dict:
    """Publish every file for one state. Returns a short summary row."""
    party_by_person = {p["id"]: p.get("party_code") for p in people}

    # Votes
    for v in votes:
        pub.publish_json(vote_key(v), v)
    groups: dict[str, list[dict]] = defaultdict(list)
    for v in votes:
        groups[v["session"]].append(summary(v))
    for session, rows in groups.items():
        rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
        pub.publish_json(f"v1/state-votes/{code}/{session_slug(session)}.json",
                         {"state": code, "session": session, "votes": rows})
    latest = sorted((summary(v) for v in votes), key=lambda r: (r["date"] or "", r["id"]), reverse=True)[:100]
    pub.publish_json(f"v1/state-votes/{code}/latest.json", {"state": code, "votes": latest})

    # Bills that have roll call votes
    votes_by_bill: dict[str, list[dict]] = defaultdict(list)
    for v in votes:
        votes_by_bill[v["bill"]["id"]].append(summary(v))
    for bid, rows in votes_by_bill.items():
        bill = bills.get(bid)
        if not bill:
            continue
        rows.sort(key=lambda r: r["date"] or "")
        pub.publish_json(bill_key(code, bill["session"], bid), {**bill, "roll_call_votes": rows})

    # Legislators
    member_stats = stats_b.compute(votes, lambda pid: party_by_person.get(pid or ""), id_key="person")
    histories = member_histories(votes, id_key="person", key_fn=vote_key)
    for p in people:
        history = histories.get(p["id"], [])
        pub.publish_json(f"v1/legislators/{p['id']}.json", {
            **card(p),
            **{k: p.get(k) for k in ("given_name", "family_name", "email", "birth_date", "biography",
                                     "offices", "links", "social")},
            "state_name": name,
            "stats": member_stats.get(p["id"], {}).get(p["chamber"]),
            "recent_votes": history[:RECENT],
            "links_app": {"votes": f"legislators/{p['id']}/votes.json"},
            "sources": [SOURCE] + [{"name": "Official source", "url": u} for u in p.get("sources", [])[:3]],
        })
        pub.publish_json(f"v1/legislators/{p['id']}/votes.json", {"id": p["id"], "votes": history})

    # Chamber rosters
    chambers: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for p in people:
        chambers[p["chamber"]][p["district"] or ""].append(card(p))
    seats = info.get("seats", {})
    roster = {}
    for chamber, districts in chambers.items():
        roster[chamber] = {
            "name": info.get("chamber_names", {}).get(chamber),
            "seats": seats.get(chamber),
            "members": len([m for ms in districts.values() for m in ms]),
            "districts": [
                {"district": d, "members": sorted(ms, key=lambda m: m["name"] or "")}
                for d, ms in sorted(districts.items(), key=lambda kv: _district_sort(kv[0]))
            ],
        }
    dates = [v["date"] for v in votes if v.get("date")]
    coverage = {
        "roll_calls": len(votes),
        "with_names": sum(1 for v in votes if v["named_votes"]),
        "from": min(dates) if dates else None,
        "to": max(dates) if dates else None,
        "last_checked": last_checked,
    }
    pub.publish_json(f"v1/states/{code}/legislature.json", {
        "state": code,
        "name": name,
        "sessions": info.get("sessions", []),
        "chambers": roster,
        "coverage": coverage,
        "source": SOURCE,
    })
    return {"state": code, "name": name, "legislators": len(people), "sessions": info.get("sessions", []),
            "coverage": coverage}
