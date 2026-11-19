"""Member profile, member index, and per-state lookup files."""

from __future__ import annotations

from datetime import date

from votetracker.sources.legislators import SOURCE as LEGISLATORS_SOURCE
from votetracker.util import party_code

PHOTO = "https://unitedstates.github.io/images/congress/{size}/{bioguide}.jpg"


def _title(term: dict, jur: dict | None) -> str:
    if term["type"] == "sen":
        return "Senator"
    if term["state"] == "PR" and (not jur or jur["kind"] != "state"):
        return "Resident Commissioner"
    if jur and jur["kind"] != "state":
        return "Delegate"
    return "Representative"


def _years_served(terms: list[dict], today: date) -> float:
    days = 0
    for t in terms:
        start = date.fromisoformat(t["start"])
        end = min(date.fromisoformat(t["end"]), today)
        if end > start:
            days += (end - start).days
    return round(days / 365.25, 1)


def summary(m: dict, jurisdictions: dict[str, dict]) -> dict:
    """Compact member card used in lists and lookups."""
    term = m["terms"][-1]
    jur = jurisdictions.get(term["state"])
    name = m["name"]
    display = name.get("official_full") or f"{name.get('nickname') or name['first']} {name['last']}"
    return {
        "bioguide": m["id"]["bioguide"],
        "name": display,
        "last_name": name["last"],
        "party": term.get("party"),
        "party_code": party_code(term.get("party")),
        "chamber": "senate" if term["type"] == "sen" else "house",
        "title": _title(term, jur),
        "state": term["state"],
        "district": term.get("district") if term["type"] == "rep" else None,
        "votes_on_floor": term["type"] == "sen" or bool(jur and jur["kind"] == "state"),
        "photo_url": PHOTO.format(size="225x275", bioguide=m["id"]["bioguide"]),
    }


def profile(m: dict, legs, jurisdictions: dict[str, dict], stats: dict | None, recent_votes: list[dict],
            congress: int, today: date | None = None) -> dict:
    today = today or date.today()
    bid = m["id"]["bioguide"]
    term = m["terms"][-1]
    chamber = "senate" if term["type"] == "sen" else "house"
    card = summary(m, jurisdictions)
    jur = jurisdictions.get(term["state"], {})
    same_chamber = [t for t in m["terms"] if t["type"] == term["type"]]
    ids = m["id"]

    return {
        **card,
        "name_parts": {k: m["name"].get(k) for k in ("first", "middle", "last", "suffix", "nickname") if m["name"].get(k)},
        "state_name": jur.get("name"),
        "caucus": term.get("caucus"),
        "senate_class": term.get("class"),
        "state_rank": term.get("state_rank"),
        "term": {"start": term["start"], "end": term["end"]},
        "in_chamber_since": min(t["start"] for t in same_chamber),
        "in_congress_since": min(t["start"] for t in m["terms"]),
        "years_in_congress": _years_served(m["terms"], today),
        "terms_served": len(m["terms"]),
        "birthday": (m.get("bio") or {}).get("birthday"),
        "photo_url_large": PHOTO.format(size="450x550", bioguide=bid),
        "contact": {
            "phone": term.get("phone"),
            "office": term.get("office"),
            "address": term.get("address"),
            "website": term.get("url"),
            "contact_form": term.get("contact_form"),
            "rss": term.get("rss_url"),
            "switchboard": "(202) 224-3121",
        },
        "district_offices": [
            {k: o.get(k) for k in ("address", "suite", "building", "city", "state", "zip", "phone", "fax", "hours", "latitude", "longitude") if o.get(k) is not None}
            for o in legs.offices.get(bid, [])
        ],
        "social": legs.social.get(bid, {}),
        "committees": sorted(
            legs.committees_by_member.get(bid, []),
            key=lambda c: (c.get("parent_id") is not None, c["name"]),
        ),
        "ids": {
            "bioguide": bid,
            "lis": ids.get("lis"),
            "govtrack": ids.get("govtrack"),
            "fec": ids.get("fec", []),
            "opensecrets": ids.get("opensecrets"),
            "votesmart": ids.get("votesmart"),
            "ballotpedia": ids.get("ballotpedia"),
            "wikipedia": ids.get("wikipedia"),
            "icpsr": ids.get("icpsr"),
        },
        "links": {
            "bioguide": f"https://bioguide.congress.gov/search/bio/{bid}",
            "votes_this_congress": f"members/{bid}/votes-{congress}.json",
        },
        "stats": {"congress": congress, "chamber": chamber, **(stats or {})} if stats else None,
        "recent_votes": recent_votes,
        "sources": [LEGISLATORS_SOURCE, {"name": "Biographical Directory of the U.S. Congress", "url": f"https://bioguide.congress.gov/search/bio/{bid}"}],
    }


def state_file(j: dict, legs, jurisdictions: dict[str, dict]) -> dict:
    def card(bid):
        m = legs.by_bioguide.get(bid)
        return summary(m, jurisdictions) if m else None

    house = []
    filled = {}
    for bid in j["representatives"]:
        m = legs.by_bioguide[bid]
        filled[int(m["terms"][-1].get("district") or 0)] = card(bid)
    seats = j["house_seats"]
    # One seat = at-large (district 0). Otherwise districts 1..N.
    districts = [0] if seats <= 1 else list(range(1, seats + 1))
    for d in sorted(set(districts) | set(filled)):
        house.append({"district": d, "member": filled.get(d), "vacant": d not in filled})

    return {
        "code": j["code"],
        "name": j["name"],
        "kind": j["kind"],
        "fips": j["fips"],
        "voting_representation": j["voting_representation"],
        "senators": [c for c in (card(b) for b in j["senators"]) if c],
        "house": house,
    }
