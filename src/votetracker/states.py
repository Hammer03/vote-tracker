"""State legislature update: legislators daily, votes within a daily API budget.

Work is spread fairly: each run checks the states that were checked longest
ago (and states with a backlog first), so all 52 legislatures stay current
even on a free API key. A higher Open States tier simply means fresher data.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from votetracker import jurisdictions as jurisdictions_mod
from votetracker.builders import states as states_b
from votetracker.sources import openstates as os_src
from votetracker.util import iso

log = logging.getLogger(__name__)

# Open States covers all 50 states, DC, and Puerto Rico.
EXTRA_CODES = ("DC", "PR")
JUR_KEY = "state/openstates/jurisdictions.json"
CURSORS_KEY = "state/openstates/cursors.json"
SUMMARY_KEY = "state/openstates/summary.json"


def people_key(code: str) -> str:
    return f"state/openstates/people/{code}.json"


def votes_key(code: str, session: str) -> str:
    return f"state/openstates/votes/{code}/{os_src.session_slug(session)}.jsonl.gz"


def bills_key(code: str, session: str) -> str:
    return f"state/openstates/bills/{code}/{os_src.session_slug(session)}.json.gz"


def default_codes() -> list[str]:
    base = jurisdictions_mod.load_base()
    return [j["code"] for j in base if j["kind"] == "state" or j["code"] in EXTRA_CODES]


class StatesRunner:
    def __init__(self, cfg, store, http, now: datetime):
        self.cfg = cfg
        self.store = store
        self.http = http
        self.now = now
        self.today: date = now.date()
        if hasattr(http, "host_intervals"):
            http.host_intervals["v3.openstates.org"] = 60.0 / max(1, cfg.openstates_per_minute) + 0.5
        self.added: list[dict] = []  # votes seen for the first time this run
        self.dirty: set[str] = set()

    # ------------------------------------------------------------ inputs

    def _jurisdictions(self, api) -> dict[str, dict]:
        cached = self.store.get_json(JUR_KEY, {}) or {}
        if cached.get("fetched_on") == self.today.isoformat() or api is None:
            return cached.get("items", {})
        try:
            rows = api.jurisdictions()
        except (os_src.BudgetExhausted, RuntimeError) as exc:
            log.warning("Open States jurisdictions: %s", exc)
            return cached.get("items", {})
        items = {}
        for j in rows:
            code = os_src.code_from_ocd(j.get("division_id")) or os_src.code_from_ocd(j.get("id"))
            if not code:
                continue
            seats, names = {}, {}
            for org in j.get("organizations") or []:
                cls = org.get("classification")
                if cls in ("upper", "lower", "legislature"):
                    names[cls] = org.get("name")
                    districts = org.get("districts") or []
                    if districts:
                        seats[cls] = sum(int(d.get("maximum_memberships") or 1) for d in districts)
            items[code] = {
                "id": j["id"],
                "name": j.get("name"),
                "sessions": os_src.active_sessions(j, self.today),
                "seats": seats,
                "chamber_names": names,
            }
        self.store.put_json(JUR_KEY, {"fetched_on": self.today.isoformat(), "items": items}, "no-store")
        return items

    def _people(self, code: str, fetch: bool) -> list[dict]:
        cached = self.store.get_json(people_key(code), {}) or {}
        if not fetch or cached.get("fetched_on") == self.today.isoformat():
            return cached.get("people", [])
        people = os_src.fetch_people(self.http, code)
        if people is None:
            log.warning("%s: legislator list unavailable; keeping the last copy", code)
            return cached.get("people", [])
        people.sort(key=lambda p: (p["chamber"], p["district"] or "", p["name"]))
        if people != cached.get("people"):
            self.dirty.add(code)
        self.store.put_json(people_key(code), {"fetched_on": self.today.isoformat(), "people": people}, "no-store")
        return people

    # ------------------------------------------------------------ votes

    def _fetch_votes(self, api, budget, jur: dict[str, dict], codes: list[str], people_by_code: dict) -> None:
        cursors = self.store.get_json(CURSORS_KEY, {}) or {}
        jobs = []
        for code in codes:
            for s in jur.get(code, {}).get("sessions", []):
                c = cursors.setdefault(code, {}).setdefault(s["identifier"], {})
                c.setdefault("since", os_src.initial_since(s, self.today, self.cfg.openstates_backfill_days))
                c.setdefault("page", 1)
                jobs.append((not c.get("backlog", True), c.get("last_checked") or "", code, s["identifier"]))
        jobs.sort()  # backlog first, then the longest-unchecked

        bundles: dict[tuple[str, str], dict[str, dict]] = {}
        bill_caches: dict[tuple[str, str], dict[str, dict]] = {}
        try:
            for _, _, code, session in jobs:
                if budget.remaining_this_run <= 0:
                    break
                c = cursors[code][session]
                page = api.bills_page(code, session, c["since"], c["page"])
                c["last_checked"] = iso(self.now)
                if page is None:  # page past the end
                    c["page"] = 1
                    c["backlog"] = False
                    continue
                results = page.get("results", [])
                meta = page.get("pagination", {})
                key = (code, session)
                if key not in bundles:
                    bundles[key] = {v["id"]: v for v in self.store.get_jsonl_gz(votes_key(code, session))}
                    bill_caches[key] = self.store.get_json_gz(bills_key(code, session), {}) or {}
                party_by_person = {p["id"]: p.get("party_code") for p in people_by_code.get(code, [])}
                for bill in results:
                    nb = os_src.normalize_bill(bill, code)
                    bill_caches[key][nb["id"]] = nb
                    for ve in bill.get("votes") or []:
                        v = os_src.normalize_vote(bill, ve, code, party_by_person)
                        old = bundles[key].get(v["id"])
                        if old != v:
                            self.dirty.add(code)
                            if old is None:
                                self.added.append(v)
                        bundles[key][v["id"]] = v
                if results:
                    last = results[-1].get("updated_at") or c["since"]
                    if last == c["since"]:
                        c["page"] += 1  # many bills share one timestamp: step through pages
                    else:
                        c["since"], c["page"] = last, 1
                c["backlog"] = (meta.get("max_page") or 1) > (meta.get("page") or 1)
        except os_src.BudgetExhausted:
            log.info("Open States: daily budget reached for this run")
        except RuntimeError as exc:
            log.warning("Open States: %s", exc)
        finally:
            for (code, session), votes in bundles.items():
                rows = sorted(votes.values(), key=lambda v: (v["date"] or "", v["id"]))
                self.store.put_jsonl_gz(votes_key(code, session), rows)
                self.store.put_json_gz(bills_key(code, session), bill_caches[(code, session)])
            self.store.put_json(CURSORS_KEY, cursors, "no-store")

    # ------------------------------------------------------------ run

    def run(self, pub, only: set[str] | None = None, fetch: bool = True, rebuild_all: bool = False) -> dict:
        codes = [c for c in default_codes() if not only or c in only]
        api = budget = None
        if self.cfg.openstates_api_key and fetch:
            budget = os_src.Budget(self.store, self.cfg.openstates_daily_budget, self.now)
            api = os_src.OpenStatesAPI(self.http, self.cfg.openstates_api_key, budget)
        elif fetch:
            log.warning("OPENSTATES_API_KEY not set: publishing state legislators only, no state votes")

        jur = self._jurisdictions(api)
        people_by_code = {code: self._people(code, fetch) for code in codes}
        if api:
            self._fetch_votes(api, budget, jur, codes, people_by_code)
            budget.save()

        names = {j["code"]: j["name"] for j in jurisdictions_mod.load_base()}
        summary = self.store.get_json(SUMMARY_KEY, {}) or {}
        cursors = self.store.get_json(CURSORS_KEY, {}) or {}
        built = 0
        for code in codes:
            if not (rebuild_all or code in self.dirty or code not in summary):
                continue
            info = jur.get(code, {})
            votes, bills = [], {}
            for s in info.get("sessions", []):
                votes += self.store.get_jsonl_gz(votes_key(code, s["identifier"]))
                bills.update(self.store.get_json_gz(bills_key(code, s["identifier"]), {}) or {})
            checks = [c.get("last_checked") for c in cursors.get(code, {}).values() if c.get("last_checked")]
            summary[code] = states_b.build_state(pub, code, names.get(code, code), info,
                                                 people_by_code.get(code, []), votes, bills,
                                                 max(checks) if checks else None)
            built += 1
        self.store.put_json(SUMMARY_KEY, summary, "no-store")
        pub.publish_json("v1/state-legislatures.json",
                         {"legislatures": [summary[c] for c in sorted(summary)], "source": os_src.SOURCE})
        report = {
            "states_rebuilt": built,
            "state_votes_added": len(self.added),
            "openstates_requests_today": budget.used if budget else None,
            "openstates_daily_budget": budget.daily if budget else None,
        }
        log.info("states: %s", report)
        return report

    def names(self, codes: list[str] | None = None) -> dict[str, dict]:
        """{person_id: legislator} for people seen this run (used for alerts)."""
        out = {}
        for code in codes or default_codes():
            for p in (self.store.get_json(people_key(code), {}) or {}).get("people", []):
                out[p["id"]] = p
        return out
