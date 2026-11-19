"""Bills, official summaries, and bill text links from the Congress.gov API.

Docs: https://github.com/LibraryOfCongress/api.congress.gov
Needs a free api.data.gov key (CONGRESS_GOV_API_KEY). Limit: 5,000/hour.

Summaries are written by the Congressional Research Service (CRS), which is
nonpartisan. We use them instead of AI summaries: they are official and free.
"""

from __future__ import annotations

import logging

from votetracker.util import BILL_LABELS, bill_id, congress_gov_bill_url, html_to_text, iso, party_code, utc_now

log = logging.getLogger(__name__)

API = "https://api.congress.gov/v3"
SOURCE = {"name": "Congress.gov (Library of Congress)", "url": "https://www.congress.gov"}


class CongressGov:
    def __init__(self, http, api_key: str):
        self.http = http
        self.api_key = api_key

    def _get(self, path: str, **params):
        resp = self.http.get(f"{API}{path}", params={"api_key": self.api_key, "format": "json", **params})
        if resp.status == 404:
            return None
        if not resp.ok:
            raise RuntimeError(f"Congress.gov {path}: HTTP {resp.status}")
        return resp.json()

    def bill(self, congress: int, btype: str, number: int) -> dict | None:
        base = f"/bill/{congress}/{btype}/{number}"
        data = self._get(base)
        if not data or "bill" not in data:
            return None
        b = data["bill"]

        summaries = (self._get(f"{base}/summaries") or {}).get("summaries", []) if b.get("summaries") else []
        texts = (self._get(f"{base}/text") or {}).get("textVersions", []) if b.get("textVersions") else []
        return normalize_bill(congress, btype, number, b, summaries, texts)


def _latest_summary(summaries: list[dict]) -> dict | None:
    if not summaries:
        return None
    latest = sorted(summaries, key=lambda s: (s.get("actionDate") or "", s.get("updateDate") or ""))[-1]
    return {
        "text": html_to_text(latest.get("text")),
        "action_date": latest.get("actionDate"),
        "action": latest.get("actionDesc"),
        "version_code": latest.get("versionCode"),
        "author": "Congressional Research Service",
    }


def _text_versions(texts: list[dict]) -> list[dict]:
    out = []
    for t in texts:
        formats = {}
        for f in t.get("formats", []) or []:
            kind = (f.get("type") or "").lower()
            if "pdf" in kind:
                formats["pdf"] = f.get("url")
            elif "xml" in kind:
                formats["xml"] = f.get("url")
            elif "text" in kind or "htm" in kind:
                formats["html"] = f.get("url")
        out.append({"type": t.get("type"), "date": t.get("date"), "formats": formats})
    out.sort(key=lambda t: t.get("date") or "")
    return out


def normalize_bill(congress: int, btype: str, number: int, b: dict, summaries: list[dict], texts: list[dict]) -> dict:
    sponsors = []
    for s in b.get("sponsors", []) or []:
        sponsors.append(
            {
                "bioguide": s.get("bioguideId"),
                "name": s.get("fullName"),
                "party": party_code(s.get("party")),
                "state": s.get("state"),
                "district": s.get("district"),
            }
        )
    latest = b.get("latestAction") or {}
    laws = [{"type": l.get("type"), "number": l.get("number")} for l in (b.get("laws") or [])]
    return {
        "id": bill_id(congress, btype, number),
        "congress": congress,
        "type": btype,
        "number": number,
        "label": f"{BILL_LABELS[btype]} {number}",
        "title": b.get("title"),
        "introduced_date": b.get("introducedDate"),
        "origin_chamber": b.get("originChamber"),
        "policy_area": (b.get("policyArea") or {}).get("name"),
        "sponsors": sponsors,
        "cosponsors_count": (b.get("cosponsors") or {}).get("count", 0),
        "latest_action": {"date": latest.get("actionDate"), "text": latest.get("text")},
        "became_law": bool(laws),
        "laws": laws,
        "summary": _latest_summary(summaries),
        "text_versions": _text_versions(texts),
        "full_text_url": _text_versions(texts)[-1]["formats"].get("html") if texts else None,
        "url": congress_gov_bill_url(congress, btype, number),
        "updated": b.get("updateDate"),
        "fetched_at": iso(utc_now()),
        "source": SOURCE,
    }
