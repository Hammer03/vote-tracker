"""State legislatures (all 50 states, DC, and Puerto Rico) from Open States.

Open States collects official records from every state legislature's own
website and publishes them in one format. Data is public domain.

* Legislators: nightly CSV files, no key needed.
  https://data.openstates.org/people/current/{abbr}.csv
* Bills and roll call votes: API v3, needs a free key.
  https://v3.openstates.org/bills?jurisdiction=oh&session=...&include=votes

Free API keys allow 10 requests a minute and 250 a day (higher tiers can be
requested from Plural Policy). A daily budget is tracked so hourly runs share
the allowance fairly across states and never go over.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta

from votetracker.sources.common import normalize_position, party_tallies, tally
from votetracker.util import party_code

log = logging.getLogger(__name__)

API = "https://v3.openstates.org"
PEOPLE_CSV = "https://data.openstates.org/people/current/{abbr}.csv"
SOURCE = {"name": "Open States (Plural Policy), from official state legislature websites",
          "url": "https://open.pluralpolicy.com"}
USAGE_KEY = "state/openstates/usage.json"

_CODE_RE = re.compile(r"(?:state|district|territory):([a-z]{2})")

# What members of each chamber are called. Upper chambers are Senates
# everywhere; lower chambers differ by state.
LOWER_TITLES = {
    "CA": "Assembly Member", "NV": "Assembly Member", "NY": "Assembly Member",
    "WI": "Assembly Member", "NJ": "Assembly Member",
    "MD": "Delegate", "VA": "Delegate", "WV": "Delegate",
}
UNICAMERAL_TITLES = {"NE": "State Senator", "DC": "Councilmember"}


class BudgetExhausted(Exception):
    pass


def code_from_ocd(ocd_id: str | None) -> str | None:
    m = _CODE_RE.search(ocd_id or "")
    return m.group(1).upper() if m else None


def uuid_of(ocd_id: str | None) -> str | None:
    if not ocd_id:
        return None
    return ocd_id.rsplit("/", 1)[-1] or None


def session_slug(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", identifier).strip("_") or "session"


def title_for(code: str, chamber: str) -> str:
    if chamber == "legislature":
        return UNICAMERAL_TITLES.get(code, "Legislator")
    if chamber == "upper":
        return "State Senator"
    return LOWER_TITLES.get(code, "State Representative")


# ---------------------------------------------------------------- budget

class Budget:
    """Daily API allowance shared by all runs in a UTC day."""

    def __init__(self, store, daily: int, now: datetime):
        self.store = store
        self.daily = daily
        self.now = now
        data = store.get_json(USAGE_KEY, {}) or {}
        today = now.date().isoformat()
        self.used = data.get("used", 0) if data.get("date") == today else 0
        self.date = today
        self.run_used = 0
        self.run_limit = self._run_share()

    def _run_share(self) -> int:
        remaining = max(0, self.daily - self.used)
        runs_left = max(1, 24 - self.now.hour)
        share = remaining // runs_left
        return max(min(remaining, 1), share)

    @property
    def remaining_this_run(self) -> int:
        return max(0, min(self.run_limit - self.run_used, self.daily - self.used))

    def take(self) -> None:
        if self.remaining_this_run <= 0:
            raise BudgetExhausted()
        self.used += 1
        self.run_used += 1

    def save(self) -> None:
        self.store.put_json(USAGE_KEY, {"date": self.date, "used": self.used, "daily": self.daily}, "no-store")


# ---------------------------------------------------------------- API client

class OpenStatesAPI:
    def __init__(self, http, api_key: str, budget: Budget):
        self.http = http
        self.api_key = api_key
        self.budget = budget

    def _get(self, path: str, params: list[tuple[str, str]]):
        self.budget.take()
        resp = self.http.get(f"{API}{path}", params=params, headers={"X-API-KEY": self.api_key})
        if resp.status == 404:
            return None
        if resp.status == 429:
            # Out of allowance on Open States' side: stop for this run.
            self.budget.used = self.budget.daily
            raise BudgetExhausted()
        if not resp.ok:
            raise RuntimeError(f"Open States {path}: HTTP {resp.status}")
        return resp.json()

    def jurisdictions(self) -> list[dict]:
        data = self._get("/jurisdictions", [("classification", "state"), ("include", "legislative_sessions"),
                                             ("include", "organizations"), ("per_page", "52")])
        return (data or {}).get("results", [])

    def bills_page(self, abbr: str, session: str, since: str, page: int) -> dict | None:
        params = [
            ("jurisdiction", abbr.lower()),
            ("session", session),
            ("updated_since", since),
            ("sort", "updated_asc"),
            ("per_page", "20"),
            ("page", str(page)),
        ]
        for inc in ("votes", "sponsorships", "abstracts", "versions", "sources"):
            params.append(("include", inc))
        return self._get("/bills", params)


# ---------------------------------------------------------------- people

def fetch_people(http, abbr: str) -> list[dict] | None:
    """Current legislators for one state from the nightly CSV (no key needed)."""
    for variant in (abbr.lower(), abbr.upper()):
        resp = http.get(PEOPLE_CSV.format(abbr=variant))
        if resp.ok and resp.content.strip():
            rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
            return [normalize_person(r, abbr.upper()) for r in rows if r.get("id")]
    return None


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def normalize_person(row: dict, code: str) -> dict:
    chamber = (row.get("current_chamber") or "").strip() or "legislature"
    offices = []
    for kind in ("capitol", "district"):
        office = {
            "type": kind,
            "address": (row.get(f"{kind}_address") or "").strip() or None,
            "phone": (row.get(f"{kind}_voice") or "").strip() or None,
            "fax": (row.get(f"{kind}_fax") or "").strip() or None,
        }
        if office["address"] or office["phone"]:
            offices.append(office)
    social = {k: row[k].strip() for k in ("twitter", "youtube", "instagram", "facebook") if (row.get(k) or "").strip()}
    party = (row.get("current_party") or "").strip() or None
    return {
        "id": uuid_of(row["id"]),
        "ocd_id": row["id"],
        "name": (row.get("name") or "").strip(),
        "given_name": (row.get("given_name") or "").strip() or None,
        "family_name": (row.get("family_name") or "").strip() or None,
        "party": party,
        "party_code": party_code(party),
        "state": code,
        "chamber": chamber,
        "title": title_for(code, chamber),
        "district": (row.get("current_district") or "").strip() or None,
        "image": (row.get("image") or "").strip() or None,
        "email": (row.get("email") or "").strip() or None,
        "birth_date": (row.get("birth_date") or "").strip() or None,
        "biography": (row.get("biography") or "").strip() or None,
        "offices": offices,
        "links": _split(row.get("links")),
        "sources": _split(row.get("sources")),
        "social": social,
    }


# ---------------------------------------------------------------- sessions

def active_sessions(jurisdiction: dict, today: date, grace_days: int = 30) -> list[dict]:
    """Sessions in progress (or ended within the grace period).

    If none are active, the most recent session that has started is used,
    so the app still shows the latest record between sessions.
    """
    sessions = jurisdiction.get("legislative_sessions") or []
    started = [s for s in sessions if s.get("start_date") and s["start_date"][:10] <= today.isoformat()]
    cutoff = (today - timedelta(days=grace_days)).isoformat()
    active = [s for s in started if not s.get("end_date") or s["end_date"][:10] >= cutoff]
    if not active and started:
        active = [max(started, key=lambda s: s["start_date"])]
    return [
        {"identifier": s["identifier"], "name": s.get("name"), "classification": s.get("classification"),
         "start_date": s.get("start_date"), "end_date": s.get("end_date") or None}
        for s in active
    ]


# ---------------------------------------------------------------- bills & votes

RESULT_WORDS = {"pass": "Passed", "fail": "Failed"}


def _best_link(links: list[dict]) -> str | None:
    by_type = {(l.get("media_type") or "").lower(): l.get("url") for l in links or []}
    for t in ("text/html", "application/pdf"):
        if by_type.get(t):
            return by_type[t]
    return (links or [{}])[0].get("url") if links else None


def normalize_bill(bill: dict, code: str) -> dict:
    versions = []
    for v in bill.get("versions") or []:
        versions.append({"note": v.get("note"), "date": v.get("date"),
                         "links": [{"url": l.get("url"), "media_type": l.get("media_type")} for l in v.get("links") or []]})
    versions.sort(key=lambda v: v.get("date") or "")
    sponsors = []
    for s in bill.get("sponsorships") or []:
        person = s.get("person") or {}
        sponsors.append({
            "name": s.get("name"),
            "primary": bool(s.get("primary")),
            "classification": s.get("classification"),
            "person": uuid_of(person.get("id")),
            "party": party_code(person.get("party")),
        })
    return {
        "id": uuid_of(bill["id"]),
        "state": code,
        "session": bill.get("session"),
        "identifier": bill.get("identifier"),
        "label": bill.get("identifier"),
        "title": bill.get("title"),
        "classification": bill.get("classification") or [],
        "subjects": bill.get("subject") or [],
        "chamber": (bill.get("from_organization") or {}).get("classification"),
        "abstracts": [{"text": a.get("abstract"), "note": a.get("note")} for a in bill.get("abstracts") or []],
        "sponsors": sponsors,
        "first_action_date": bill.get("first_action_date") or None,
        "latest_action": {"date": bill.get("latest_action_date") or None,
                          "text": bill.get("latest_action_description") or None},
        "latest_passage_date": bill.get("latest_passage_date") or None,
        "versions": versions,
        "full_text_url": _best_link(versions[-1]["links"]) if versions else None,
        "sources": [s.get("url") for s in bill.get("sources") or [] if s.get("url")],
        "openstates_url": bill.get("openstates_url"),
        "updated_at": bill.get("updated_at"),
        "source": SOURCE,
    }


def normalize_vote(bill: dict, ve: dict, code: str, party_by_person: dict[str, str]) -> dict:
    org = ve.get("organization") or {}
    start = ve.get("start_date") or ""
    positions = []
    for pv in ve.get("votes") or []:
        voter = pv.get("voter") or {}
        pid = uuid_of(voter.get("id"))
        positions.append({
            "person": pid,
            "name": pv.get("voter_name") or voter.get("name"),
            "party": party_code(voter.get("party")) or party_by_person.get(pid or ""),
            "vote": normalize_position(pv.get("option")),
            "raw": pv.get("option"),
        })
    official = {c.get("option"): c.get("value") for c in ve.get("counts") or [] if c.get("option")}
    if positions:
        totals = tally(positions)
    else:
        totals = tally([])
        for option, value in official.items():
            totals[normalize_position(option)] += int(value or 0)
    vote_sources = [s.get("url") for s in ve.get("sources") or [] if s.get("url")]
    bill_sources = [s.get("url") for s in bill.get("sources") or [] if s.get("url")]
    vuuid = uuid_of(ve.get("id"))
    return {
        "id": f"{code.lower()}-{vuuid}",
        "uuid": vuuid,
        "state": code,
        "session": bill.get("session"),
        "chamber": org.get("classification") or "legislature",
        "chamber_name": org.get("name"),
        "date": start[:10] or None,
        "datetime": start if len(start) > 10 else None,
        "question": ve.get("motion_text"),
        "motion_types": ve.get("motion_classification") or [],
        "result": RESULT_WORDS.get((ve.get("result") or "").lower(), ve.get("result")),
        "identifier": ve.get("identifier") or None,
        "description": bill.get("title"),
        "bill": {"id": uuid_of(bill["id"]), "label": bill.get("identifier"), "title": bill.get("title"),
                 "session": bill.get("session")},
        "named_votes": bool(positions),
        "positions": positions,
        "totals": totals,
        "official_counts": official,
        "party_totals": party_tallies(positions),
        "source": {
            "name": f"Official records via {SOURCE['name'].split(',')[0]}",
            "url": (vote_sources or bill_sources or [bill.get("openstates_url")])[0],
            "openstates_url": bill.get("openstates_url"),
        },
    }


def initial_since(session: dict, today: date, backfill_days: int) -> str:
    start = (session.get("start_date") or today.isoformat())[:10]
    floor = (today - timedelta(days=backfill_days)).isoformat()
    return max(start, floor)
