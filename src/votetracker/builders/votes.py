"""Vote detail files, vote indexes, the latest-votes feed, and member histories."""

from __future__ import annotations

from collections import defaultdict


def vote_key(v: dict) -> str:
    return f"v1/votes/{v['chamber']}/{v['congress']}-{v['session']}/{v['number']}.json"


def summary(v: dict) -> dict:
    """One row in a vote list."""
    return {
        "id": v["id"],
        "chamber": v["chamber"],
        "congress": v["congress"],
        "session": v["session"],
        "number": v["number"],
        "date": v["date"],
        "datetime": v["datetime"],
        "question": v["question"],
        "description": v["description"],
        "result": v["result"],
        "required": v.get("required"),
        "bill": {"id": v["bill"]["id"], "label": v["bill"]["label"]} if v.get("bill") else None,
        "nomination": {"id": v["nomination"]["id"]} if v.get("nomination") else None,
        "amendment": v["amendment"]["number"] if v.get("amendment") else None,
        "totals": v["totals"],
        "path": vote_key(v)[len("v1/"):],
    }


def detail(v: dict, bill_titles: dict[str, str] | None = None) -> dict:
    out = dict(v)
    if v.get("bill") and bill_titles:
        title = bill_titles.get(v["bill"]["id"])
        if title:
            out["bill"] = {**v["bill"], "title": title}
    out["positions"] = sorted(v["positions"], key=lambda p: (p.get("state") or "", p.get("name") or ""))
    return out


def session_indexes(votes: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for v in votes:
        groups[f"v1/votes/{v['chamber']}/{v['congress']}-{v['session']}.json"].append(summary(v))
    for rows in groups.values():
        rows.sort(key=lambda r: r["number"], reverse=True)
    return dict(groups)


def latest(votes: list[dict], limit: int = 100) -> list[dict]:
    rows = [summary(v) for v in votes]
    rows.sort(key=lambda r: (r["datetime"] or "", r["chamber"], r["number"]), reverse=True)
    return rows[:limit]


def member_histories(votes: list[dict], id_key: str = "bioguide", key_fn=None) -> dict[str, list[dict]]:
    """{member_id: [rows newest first]} — each row is one roll call and the member's position."""
    key_fn = key_fn or vote_key
    out: dict[str, list[dict]] = defaultdict(list)
    for v in votes:
        base = {
            "id": v["id"],
            "chamber": v["chamber"],
            "date": v["date"],
            "datetime": v.get("datetime"),
            "question": v["question"],
            "description": v.get("description"),
            "result": v["result"],
            "bill": v["bill"]["label"] if v.get("bill") else None,
            "bill_id": v["bill"]["id"] if v.get("bill") else None,
            "path": key_fn(v)[len("v1/"):],
        }
        for p in v["positions"]:
            if p.get(id_key):
                out[p[id_key]].append({**base, "position": p["vote"], "position_raw": p.get("raw")})
    for rows in out.values():
        rows.sort(key=lambda r: (r["datetime"] or r["date"] or "", r["id"]), reverse=True)
    return dict(out)


def votes_by_bill(votes: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for v in votes:
        if v.get("bill"):
            out[v["bill"]["id"]].append(summary(v))
    for rows in out.values():
        rows.sort(key=lambda r: (r["datetime"] or ""))
    return dict(out)
