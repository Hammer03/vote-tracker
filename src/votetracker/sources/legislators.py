"""Members of Congress from the public-domain congress-legislators project.

https://github.com/unitedstates/congress-legislators (CC0 / public domain)

Provides names, terms, parties, contact info, social accounts, district
offices, committee assignments, and ID cross-walks (Bioguide, Senate LIS,
FEC, GovTrack, Vote Smart, ...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import yaml

from votetracker.util import party_code

log = logging.getLogger(__name__)

BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
FILES = {
    "current": f"{BASE}/legislators-current.yaml",
    "historical": f"{BASE}/legislators-historical.yaml",
    "social": f"{BASE}/legislators-social-media.yaml",
    "offices": f"{BASE}/legislators-district-offices.yaml",
    "committees": f"{BASE}/committees-current.yaml",
    "membership": f"{BASE}/committee-membership-current.yaml",
}
SOURCE = {
    "name": "congress-legislators (public domain)",
    "url": "https://github.com/unitedstates/congress-legislators",
}

try:  # libyaml is much faster on the large files
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _Loader = yaml.SafeLoader


def _load_yaml(http, url: str):
    resp = http.get(url)
    if not resp.ok:
        raise RuntimeError(f"could not download {url} (HTTP {resp.status})")
    return yaml.load(resp.content, Loader=_Loader)


@dataclass
class Legislators:
    current: list[dict]
    social: dict[str, dict] = field(default_factory=dict)
    offices: dict[str, list[dict]] = field(default_factory=dict)
    committees: dict[str, dict] = field(default_factory=dict)  # thomas_id -> info
    membership: dict[str, list[dict]] = field(default_factory=dict)  # committee id -> members
    historical: list[dict] | None = None

    def __post_init__(self):
        self.by_bioguide = {m["id"]["bioguide"]: m for m in self.current}
        self.lis_to_bioguide = {m["id"]["lis"]: m["id"]["bioguide"] for m in self.current if m["id"].get("lis")}
        self.committees_by_member: dict[str, list[dict]] = {}
        for cid, members in self.membership.items():
            info = self.committees.get(cid)
            if not info:
                continue
            for entry in members:
                bid = entry.get("bioguide")
                if not bid:
                    continue
                self.committees_by_member.setdefault(bid, []).append(
                    {
                        "id": cid,
                        "name": info["name"],
                        "parent_id": info.get("parent_id"),
                        "chamber": info.get("type"),
                        "title": entry.get("title"),
                        "rank": entry.get("rank"),
                        "side": entry.get("party"),  # "majority" / "minority"
                    }
                )

    def add_historical(self, historical: list[dict]) -> None:
        self.historical = historical
        for m in historical:
            lis = m["id"].get("lis")
            if lis and lis not in self.lis_to_bioguide:
                self.lis_to_bioguide[lis] = m["id"]["bioguide"]

    def caucus_code(self, bioguide: str | None) -> str | None:
        """Party code used for 'votes with party' math.

        Independents who caucus with a party are compared with that party,
        as recorded in the source data's ``caucus`` field.
        """
        m = self.by_bioguide.get(bioguide or "")
        if not m:
            return None
        term = m["terms"][-1]
        return party_code(term.get("caucus") or term.get("party"))


def fetch(http, include_historical: bool = False) -> Legislators:
    current = _load_yaml(http, FILES["current"])
    social_rows = _load_yaml(http, FILES["social"]) or []
    office_rows = _load_yaml(http, FILES["offices"]) or []
    committee_rows = _load_yaml(http, FILES["committees"]) or []
    membership = _load_yaml(http, FILES["membership"]) or {}

    social = {r["id"]["bioguide"]: r.get("social", {}) for r in social_rows if r.get("id", {}).get("bioguide")}
    offices = {r["id"]["bioguide"]: r.get("offices", []) for r in office_rows if r.get("id", {}).get("bioguide")}

    committees: dict[str, dict] = {}
    for c in committee_rows:
        cid = c.get("thomas_id")
        if not cid:
            continue
        committees[cid] = {"name": c.get("name"), "type": c.get("type"), "url": c.get("url"), "parent_id": None}
        for sub in c.get("subcommittees", []) or []:
            sid = f"{cid}{sub.get('thomas_id')}"
            committees[sid] = {
                "name": f"{c.get('name')} — {sub.get('name')}",
                "type": c.get("type"),
                "url": c.get("url"),
                "parent_id": cid,
            }

    legs = Legislators(current=current, social=social, offices=offices, committees=committees, membership=membership)
    if include_historical:
        legs.add_historical(_load_yaml(http, FILES["historical"]) or [])
    log.info("loaded %d current members", len(current))
    return legs


def load_historical(http) -> list[dict]:
    return _load_yaml(http, FILES["historical"]) or []
