"""The update run: fetch what's new, rebuild the public files, publish changes.

Designed to run every hour in GitHub Actions. When nothing new has happened
it makes only a few small requests and uploads only meta.json.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from votetracker import SCHEMA_VERSION, __version__
from votetracker import jurisdictions as jurisdictions_mod
from votetracker.builders import members as members_b
from votetracker.builders import stats as stats_b
from votetracker.builders import votes as votes_b
from votetracker import notify
from votetracker.publish import Publisher
from votetracker.money import MoneyRunner
from votetracker.states import StatesRunner
from votetracker.sources import congress_gov, house_votes, legislators, senate_votes, voteview
from votetracker.util import congress_for_year, iso, make_bill_ref, utc_now, years_for_congress

TITLE_ABBR = {"Senator": "Sen.", "Representative": "Rep.", "Delegate": "Del.", "Resident Commissioner": "Res. Comm."}

log = logging.getLogger(__name__)

DISCLAIMER = (
    "Vote Tracker is an independent app. It is not affiliated with, endorsed by, or acting for any "
    "government entity. Every record comes from the official public sources listed here, and each "
    "item links to its source so you can check it yourself."
)

SOURCES = [
    {"name": house_votes.SOURCE_NAME, "url": "https://clerk.house.gov/Votes"},
    {"name": senate_votes.SOURCE_NAME, "url": "https://www.senate.gov/legislative/votes.htm"},
    congress_gov.SOURCE,
    legislators.SOURCE,
    {"name": "Biographical Directory of the U.S. Congress", "url": "https://bioguide.congress.gov"},
]

MAX_BILL_FETCHES_PER_RUN = 400
RECENT_VOTES_ON_PROFILE = 20


def _bundle_key(chamber: str, congress: int) -> str:
    return f"state/votes/{chamber}/{congress}.jsonl.gz"


def _bills_key(congress: int) -> str:
    return f"state/bills/{congress}.json.gz"


class Pipeline:
    def __init__(self, cfg, store, http, today: date | None = None):
        self.cfg = cfg
        self.store = store
        self.http = http
        self.today = today or date.today()
        self.added: list[dict] = []  # votes seen for the first time this run

    # ------------------------------------------------------------------ fetch

    def _load_bundle(self, chamber: str, congress: int) -> dict[str, dict]:
        return {v["id"]: v for v in self.store.get_jsonl_gz(_bundle_key(chamber, congress))}

    def _save_bundle(self, chamber: str, congress: int, votes: dict[str, dict]) -> None:
        rows = sorted(votes.values(), key=lambda v: (v["session"], v["number"]))
        self.store.put_jsonl_gz(_bundle_key(chamber, congress), rows)

    def _fetch_house(self, congress: int, bundle: dict[str, dict], max_new, full) -> list[str]:
        new_ids = []
        for year in years_for_congress(congress):
            if year > self.today.year:
                continue
            session = 1 if year == years_for_congress(congress)[0] else 2
            known = [v["number"] for v in bundle.values() if v["session"] == session]
            # Re-check the last two votes in case the Clerk posted a correction.
            start = 1 if (full or not known) else max(1, max(known) - 1)
            for v in house_votes.fetch_new(self.http, year, start, max_new=max_new):
                if v["id"] not in bundle:
                    self.added.append(v)
                if bundle.get(v["id"]) != v:
                    new_ids.append(v["id"])
                bundle[v["id"]] = v
        return new_ids

    def _fetch_senate(self, congress: int, bundle: dict[str, dict], legs, max_new, full) -> list[str]:
        new_ids = []
        first_year, _ = years_for_congress(congress)
        blocked = None
        fill_ids: set[str] = set()  # votes senate.gov refused one at a time
        for session in (1, 2):
            if first_year + session - 1 > self.today.year:
                continue
            # Votes we only have from Voteview are re-read from senate.gov when it answers.
            known = {v["number"] for v in bundle.values()
                     if v["session"] == session and (v.get("source") or {}).get("via") != "voteview"}
            refetch = set(sorted(known)[-2:]) if known else set()
            if full:
                known = set()
            try:
                fetched = senate_votes.fetch_new(self.http, congress, session, known, legs.lis_to_bioguide,
                                                 refetch=refetch, max_new=max_new)
            except senate_votes.SenateUnavailable as exc:
                blocked = str(exc)
                break
            except senate_votes.PartialFetch as partial:
                fetched = partial.found
                fill_ids.update(f"senate-{congress}-{session}-{n}" for n in partial.failed)
            for v in fetched:
                self._map_former_senators([v], legs)
                if v["id"] not in bundle:
                    self.added.append(v)
                if bundle.get(v["id"]) != v:
                    new_ids.append(v["id"])
                bundle[v["id"]] = v

        if blocked or fill_ids:
            if blocked:
                log.warning("senate.gov unavailable (%s): using Voteview for Senate votes", blocked)
            else:
                log.info("filling %d Senate votes from Voteview that senate.gov refused", len(fill_ids))
            for v in voteview.fetch_senate(self.http, congress) or []:
                if not blocked and v["id"] not in fill_ids:
                    continue
                existing = bundle.get(v["id"])
                if existing and (existing.get("source") or {}).get("via") != "voteview":
                    continue  # keep the senate.gov record
                if existing is None:
                    self.added.append(v)
                if existing != v:
                    new_ids.append(v["id"])
                bundle[v["id"]] = v
        return new_ids

    def _map_former_senators(self, votes: list[dict], legs) -> None:
        """Fill in Bioguide IDs for senators who have since left office.

        The current-members file only covers sitting senators, so the
        (large) historical file is downloaded only when it is needed.
        """
        missing = any(not p.get("bioguide") and p.get("lis") for v in votes for p in v["positions"])
        if not missing:
            return
        if legs.historical is None:
            log.info("loading historical member data to match former senators")
            legs.add_historical(legislators.load_historical(self.http))
        for v in votes:
            for p in v["positions"]:
                if not p.get("bioguide") and p.get("lis"):
                    p["bioguide"] = legs.lis_to_bioguide.get(p["lis"])

    def _remap_senate(self, bundle: dict[str, dict], legs) -> None:
        self._map_former_senators(list(bundle.values()), legs)

    def _refresh_bills(self, congress: int, all_votes: list[dict], changed_ids: set[str]) -> dict[str, dict]:
        cache: dict[str, dict] = self.store.get_json_gz(_bills_key(congress), {}) or {}
        if not self.cfg.congress_gov_api_key:
            log.warning("CONGRESS_GOV_API_KEY not set: skipping bill titles, summaries, and text links")
            return cache
        api = congress_gov.CongressGov(self.http, self.cfg.congress_gov_api_key)
        refs = {}
        touched = set()
        for v in all_votes:
            if v.get("bill"):
                refs[v["bill"]["id"]] = v["bill"]
                if v["id"] in changed_ids:
                    touched.add(v["bill"]["id"])
        stale_before = utc_now() - timedelta(days=self.cfg.bill_refresh_days)

        def needs(bid: str) -> bool:
            cached = cache.get(bid)
            if not cached or bid in touched:
                return True
            fetched = cached.get("fetched_at")
            return not fetched or datetime.fromisoformat(fetched) < stale_before

        todo = [bid for bid in refs if needs(bid)]
        # Bills with new votes first, then never-fetched, then stale.
        todo.sort(key=lambda b: (b not in touched, b in cache, cache.get(b, {}).get("fetched_at") or ""))
        fetched = 0
        for bid in todo[:MAX_BILL_FETCHES_PER_RUN]:
            ref = refs[bid]
            try:
                bill = api.bill(ref["congress"], ref["type"], ref["number"])
            except RuntimeError as exc:
                log.warning("bill %s: %s", bid, exc)
                continue
            if bill:
                cache[bid] = bill
                fetched += 1
        if len(todo) > MAX_BILL_FETCHES_PER_RUN:
            log.info("%d bills left for the next run", len(todo) - MAX_BILL_FETCHES_PER_RUN)
        log.info("bills: %d refreshed, %d cached", fetched, len(cache))
        self.store.put_json_gz(_bills_key(congress), cache)
        return cache

    # ------------------------------------------------------------------ run

    def run(self, fetch: bool = True, bills: bool = True, congress: int | None = None,
            max_new: int | None = None, full_refresh: bool = False, dry_run: bool = False,
            states: bool = True, only_states: set[str] | None = None, send_alerts: bool = True,
            now: datetime | None = None, money: bool = True, money_force: bool = False) -> dict:
        started = utc_now()
        congress = congress or congress_for_year(self.today.year)
        legs = legislators.fetch(self.http)

        house = self._load_bundle("house", congress)
        senate = self._load_bundle("senate", congress)
        changed: list[str] = []
        if fetch:
            changed += self._fetch_house(congress, house, max_new, full_refresh)
            changed += self._fetch_senate(congress, senate, legs, max_new, full_refresh)
        self._remap_senate(senate, legs)
        if fetch and not dry_run:
            self._save_bundle("house", congress, house)
            self._save_bundle("senate", congress, senate)

        all_votes = list(house.values()) + list(senate.values())
        bill_cache = self._refresh_bills(congress, all_votes, set(changed)) if bills else (
            self.store.get_json_gz(_bills_key(congress), {}) or {}
        )

        pub = Publisher(self.store, dry_run=dry_run)
        money_summaries = {}
        if money:
            jmap = jurisdictions_mod.by_code(jurisdictions_mod.resolve(legs.current))
            money_summaries = MoneyRunner(self.store, self.http, self.today).run(
                pub, legs, lambda m: members_b.summary(m, jmap), fetch=fetch, force=money_force or full_refresh)
        self.build_and_publish(congress, legs, all_votes, bill_cache, pub, money_summaries)

        states_report = {}
        runner = None
        if states:
            now = now or utc_now()
            runner = StatesRunner(self.cfg, self.store, self.http, now)
            states_report = runner.run(pub, only=only_states, fetch=fetch, rebuild_all=full_refresh)
        pub.save_manifest()

        alerts = self._send_alerts(legs, runner, dry_run) if send_alerts else {}
        report = {"uploaded": pub.uploaded, "unchanged": pub.skipped, "upload_failed": len(pub.failed),
                  "states": states_report, "alerts": alerts}
        report.update(
            {
                "started_at": iso(started),
                "finished_at": iso(utc_now()),
                "congress": congress,
                "new_or_changed_votes": len(changed),
                "http_requests": getattr(self.http, "request_count", None),
            }
        )
        if not dry_run:
            self.store.put_json("state/last_run.json", report, "no-store")
        log.info("run report: %s", report)
        return report

    def build_and_publish(self, congress: int, legs, all_votes: list[dict], bill_cache: dict, pub: Publisher,
                          money_summaries: dict | None = None) -> None:
        money_summaries = money_summaries or {}
        jur_list = jurisdictions_mod.resolve(legs.current)
        jmap = jurisdictions_mod.by_code(jur_list)

        congress_votes = [v for v in all_votes if v["congress"] == congress]
        member_stats = stats_b.compute(congress_votes, legs.caucus_code)
        histories = votes_b.member_histories(congress_votes)
        bill_titles = {bid: b.get("title") for bid, b in bill_cache.items() if b.get("title")}

        # Votes
        for v in all_votes:
            pub.publish_json(votes_b.vote_key(v), votes_b.detail(v, bill_titles))
        for key, rows in votes_b.session_indexes(all_votes).items():
            pub.publish_json(key, {"congress": congress, "votes": rows})
        pub.publish_json("v1/votes/latest.json", {"votes": votes_b.latest(all_votes)})

        # Bills that have roll call votes
        by_bill = votes_b.votes_by_bill(all_votes)
        for bid, rows in by_bill.items():
            c, btype, num = bid.split("-")
            base = bill_cache.get(bid) or {**make_bill_ref(int(c), btype, int(num)), "title": None}
            pub.publish_json(f"v1/bills/{c}/{btype}{num}.json", {**base, "roll_call_votes": rows})

        # Members
        cards = []
        for m in legs.current:
            bid = m["id"]["bioguide"]
            chamber = "senate" if m["terms"][-1]["type"] == "sen" else "house"
            history = [r for r in histories.get(bid, []) if r["chamber"] == chamber]
            stats = member_stats.get(bid, {}).get(chamber)
            doc = members_b.profile(m, legs, jmap, stats, history[:RECENT_VOTES_ON_PROFILE], congress, self.today)
            doc["money"] = money_summaries.get(bid)
            pub.publish_json(f"v1/members/{bid}.json", doc)
            pub.publish_json(f"v1/members/{bid}/votes-{congress}.json", {"bioguide": bid, "congress": congress, "votes": history})
            cards.append(members_b.summary(m, jmap))
        cards.sort(key=lambda c: (c["state"], c["chamber"] != "senate", c["district"] or 0, c["last_name"]))
        pub.publish_json("v1/members/index.json", {"members": cards})

        # Jurisdictions and state lookups
        pub.publish_json("v1/jurisdictions.json", {"jurisdictions": jur_list})
        for j in jur_list:
            pub.publish_json(f"v1/states/{j['code']}.json", members_b.state_file(j, legs, jmap))

        newest = max((v["datetime"] or "" for v in all_votes), default=None)
        pub.publish_json(
            "v1/meta.json",
            {
                "schema_version": SCHEMA_VERSION,
                "backend_version": __version__,
                "generated_at": iso(utc_now()),
                "latest_vote_at": newest or None,
                "congress": congress,
                "counts": {
                    "members": len(legs.current),
                    "house_votes": sum(1 for v in all_votes if v["chamber"] == "house"),
                    "senate_votes": sum(1 for v in all_votes if v["chamber"] == "senate"),
                    "bills": len(by_bill),
                },
                "capitol_switchboard": "(202) 224-3121",
                "state_legislatures": "state-legislatures.json",
                "alerts": {
                    "federal_topic": "m-{bioguide}",
                    "federal_major_topic": "m-{bioguide}-major",
                    "state_topic": "s-{id}",
                    "state_major_topic": "s-{id}-major",
                },
                "disclaimer": DISCLAIMER,
                "sources": SOURCES,
            },
        )

    def _send_alerts(self, legs, runner, dry_run: bool) -> dict:
        def federal_name(bid):
            m = legs.by_bioguide.get(bid)
            if not m:
                return None
            card = members_b.summary(m, {})
            return f"{TITLE_ABBR.get(card['title'], '')} {card['name']}".strip()

        messages = notify.plan(self.added, "federal", "bioguide", federal_name,
                               lambda bid: f"members/{bid}.json", self.today)
        if runner and runner.added:
            people = runner.names()
            messages += notify.plan(runner.added, "state", "person",
                                    lambda pid: (people.get(pid) or {}).get("name"),
                                    lambda pid: f"legislators/{pid}.json", self.today)
        if dry_run:
            return {"planned": len(messages), "sent": 0, "dry_run": True}
        if self.cfg.firebase_service_account:
            sender = notify.FcmSender(self.cfg.firebase_service_account)
        else:
            sender = notify.RecordingSender()
            if messages:
                log.info("FIREBASE_SERVICE_ACCOUNT not set: %d alerts recorded, not sent", len(messages))
        result = notify.deliver(messages, sender)
        result["firebase"] = bool(self.cfg.firebase_service_account)
        if not result["firebase"]:
            result["recorded_not_sent"], result["sent"] = result["sent"], 0
        self.store.put_json("state/alerts/last_run.json", {**result, "sample": messages[:20]}, "no-store")
        return result
