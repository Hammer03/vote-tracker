"""Campaign money for every sitting member of Congress (FEC bulk files, daily).

For each member we show, in plain numbers:
* cash on hand going into this term and the latest cash on hand
* money raised and spent during this term, and where it came from
  (individuals, PACs and other committees, parties, the candidate)
* every PAC, party, or campaign committee that gave directly, by name
* outside spending for or against them, by the committee that spent it

Individual donors are shown only as totals, never by name.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from votetracker.sources import fec_bulk

log = logging.getLogger(__name__)

STATUS_KEY = "state/fec/status.json"
SUMMARIES_KEY = "state/fec/summaries.json"
TOP_ROWS = 300

NOTES = [
    "Totals come from the FEC's candidate summary files for each two-year period.",
    "Committee contributions are direct contributions from PACs, party committees, and other campaigns.",
    "Outside spending is money spent independently to support or oppose this candidate. It does not go to the campaign.",
    "Contributions from individuals are shown only as totals.",
    "When a PAC forwards individual donors' contributions (\"earmarked\"), FEC records count them as individual contributions.",
]

TOTAL_FIELDS = ("receipts", "disbursements", "from_individuals", "from_pacs_and_other_committees",
                "from_parties", "from_candidate", "loans_from_candidate")


def current_cycle(today: date) -> int:
    return today.year if today.year % 2 == 0 else today.year + 1


def cycle_before(term_start: str) -> int:
    """The election cycle that put the member in this term (2025 start -> 2024)."""
    y = int(term_start[:4])
    return y - 1 if y % 2 else y


def plan_member(m: dict, today: date) -> dict | None:
    term = m["terms"][-1]
    prefix = "S" if term["type"] == "sen" else "H"
    ids = [i for i in m["id"].get("fec", []) or [] if i.startswith(prefix)]
    if not ids:
        return None
    before = cycle_before(term["start"])
    now = current_cycle(today)
    term_cycles = list(range(before + 2, now + 1, 2)) or [now]
    return {"bioguide": m["id"]["bioguide"], "fec_ids": ids, "before": before, "term_cycles": term_cycles,
            "term": {"start": term["start"], "end": term["end"]}}


def _rows(per_committee: dict[str, dict], committees: dict[str, dict], field_filter) -> list[dict]:
    out = []
    for cmte, vals in per_committee.items():
        if not field_filter(vals):
            continue
        out.append({"committee_id": cmte, **fec_bulk.committee_label(committees.get(cmte)),
                    **{k: v for k, v in vals.items() if k in ("direct", "support", "oppose", "party_coordinated")},
                    "path": f"money/committees/{cmte}.json"})
    return out


def _merge(dicts: list[dict[str, dict]]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"direct": 0.0, "support": 0.0, "oppose": 0.0, "party_coordinated": 0.0})
    for d in dicts:
        for cmte, vals in (d or {}).items():
            for k in ("direct", "support", "oppose", "party_coordinated"):
                out[cmte][k] = round(out[cmte][k] + vals.get(k, 0.0), 2)
    return dict(out)


class MoneyRunner:
    def __init__(self, store, http, today: date):
        self.store = store
        self.http = http
        self.today = today

    def _cycle_data(self, kind: str, cycle: int) -> dict:
        return self.store.get_json_gz(f"state/fec/{kind}/{cycle}.json.gz", {}) or {}

    def _refresh(self, plans: list[dict], status: dict) -> None:
        wanted = {i for p in plans for i in p["fec_ids"]}
        cycles = sorted({c for p in plans for c in [p["before"], *p["term_cycles"]]})
        etags = status.setdefault("etags", {})
        for cycle in cycles:
            for kind in ("weball", "cm", "pas2"):
                key = f"{kind}{cycle}"
                body, etag = fec_bulk.download(self.http, kind, cycle, etags.get(key))
                if body is None:
                    continue
                if kind == "weball":
                    data = fec_bulk.parse_weball(body, wanted)
                elif kind == "cm":
                    data = fec_bulk.parse_cm(body)
                else:
                    data = fec_bulk.parse_pas2(body, wanted)
                self.store.put_json_gz(f"state/fec/{kind}/{cycle}.json.gz", data)
                if etag:
                    etags[key] = etag
                log.info("FEC %s %s: %d records", kind, cycle, len(data))

    def run(self, pub, legs, members_summary, fetch: bool = True, force: bool = False) -> dict[str, dict]:
        """Returns {bioguide: short money summary} for member profiles."""
        status = self.store.get_json(STATUS_KEY, {}) or {}
        plans = [p for p in (plan_member(m, self.today) for m in legs.current) if p]
        if not force and status.get("last_built") == self.today.isoformat():
            return self.store.get_json(SUMMARIES_KEY, {}) or {}
        if fetch:
            self._refresh(plans, status)

        cycles = sorted({c for p in plans for c in [p["before"], *p["term_cycles"]]})
        weball = {c: self._cycle_data("weball", c) for c in cycles}
        pas2 = {c: self._cycle_data("pas2", c) for c in cycles}
        committees: dict[str, dict] = {}
        for c in cycles:  # newer cycles overwrite older names
            committees.update(self._cycle_data("cm", c))

        summaries: dict[str, dict] = {}
        by_committee: dict[str, list[dict]] = defaultdict(list)
        for p in plans:
            m = legs.by_bioguide[p["bioguide"]]
            card = members_summary(m)
            cid = next((i for i in p["fec_ids"] if any(i in weball[c] for c in p["term_cycles"])), None) \
                or next((i for i in p["fec_ids"] if i in weball.get(p["before"], {})), p["fec_ids"][0])

            by_cycle = [{"cycle": c, **weball[c][cid]} for c in [p["before"], *p["term_cycles"]] if cid in weball.get(c, {})]
            pre = weball.get(p["before"], {}).get(cid)
            term_rows = [r for r in by_cycle if r["cycle"] in p["term_cycles"]]
            latest = term_rows[-1] if term_rows else pre
            start = ({"amount": pre["cash_end"], "as_of": pre["coverage_end"]} if pre
                     else ({"amount": term_rows[0]["cash_start"], "as_of": None} if term_rows else None))
            now = {"amount": latest["cash_end"], "as_of": latest["coverage_end"]} if latest else None
            totals = {f: round(sum(r.get(f, 0.0) for r in term_rows), 2) for f in TOTAL_FIELDS}

            term_giving = _merge([pas2[c].get(cid) for c in p["term_cycles"]])
            pre_giving = _merge([pas2.get(p["before"], {}).get(cid)])

            def direct_rows(g):
                rows = _rows(g, committees, lambda v: v["direct"] != 0)
                return sorted(rows, key=lambda r: -r["direct"])[:TOP_ROWS]

            def outside_rows(g):
                rows = _rows(g, committees, lambda v: v["support"] != 0 or v["oppose"] != 0)
                return sorted(rows, key=lambda r: -(abs(r["support"]) + abs(r["oppose"])))[:TOP_ROWS]

            doc = {
                **card,
                "fec_candidate_id": cid,
                "fec_url": f"https://www.fec.gov/data/candidate/{cid}/",
                "term": p["term"],
                "periods": {"election_before_term": p["before"], "this_term": p["term_cycles"]},
                "cash": {"start_of_term": start, "latest": now,
                         "change": round(now["amount"] - start["amount"], 2) if (start and now) else None},
                "this_term": totals,
                "by_cycle": by_cycle,
                "committee_contributions": {"this_term": direct_rows(term_giving),
                                            "election_before_term": direct_rows(pre_giving)},
                "outside_spending": {"this_term": outside_rows(term_giving),
                                     "election_before_term": outside_rows(pre_giving)},
                "notes": NOTES,
                "sources": [fec_bulk.SOURCE, {"name": "FEC candidate page", "url": f"https://www.fec.gov/data/candidate/{cid}/"}],
            }
            pub.publish_json(f"v1/money/{p['bioguide']}.json", doc)
            summaries[p["bioguide"]] = {
                "cash_latest": now,
                "cash_start_of_term": start,
                "raised_this_term": totals["receipts"],
                "path": f"money/{p['bioguide']}.json",
            }
            for cmte, vals in term_giving.items():
                by_committee[cmte].append({**card, **{k: vals[k] for k in ("direct", "support", "oppose", "party_coordinated")}})

        index = []
        for cmte, rows in by_committee.items():
            label = fec_bulk.committee_label(committees.get(cmte))
            tot = {k: round(sum(r[k] for r in rows), 2) for k in ("direct", "support", "oppose", "party_coordinated")}
            rows.sort(key=lambda r: -(abs(r["direct"]) + abs(r["support"]) + abs(r["oppose"])))
            pub.publish_json(f"v1/money/committees/{cmte}.json", {
                "committee_id": cmte, **label,
                "fec_url": f"https://www.fec.gov/data/committee/{cmte}/",
                "totals_to_current_members": tot,
                "members": rows,
                "period": "each member's current term",
                "sources": [fec_bulk.SOURCE],
            })
            index.append({"committee_id": cmte, "name": label["name"], "kind": label["kind"],
                          "connected_org": label["connected_org"], **tot, "members": len(rows)})
        index.sort(key=lambda r: -(abs(r["direct"]) + abs(r["support"]) + abs(r["oppose"])))
        pub.publish_json("v1/money/committees/index.json", {"committees": index, "sources": [fec_bulk.SOURCE]})

        status["last_built"] = self.today.isoformat()
        self.store.put_json(STATUS_KEY, status, "no-store")
        self.store.put_json(SUMMARIES_KEY, summaries, "no-store")
        log.info("money: %d members, %d committees", len(summaries), len(index))
        return summaries
