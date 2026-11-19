"""States, DC, and territories — loaded from data, never hard-coded.

Any jurisdiction with sitting U.S. senators in the official member data is
treated as a state. So if DC or Puerto Rico become states, the apps update as
soon as their senators appear in the source data, with no code change.
Brand-new jurisdictions that are not in the YAML file are added too.
"""

from __future__ import annotations

from importlib import resources

import yaml


def load_base() -> list[dict]:
    raw = resources.files("votetracker").joinpath("data/jurisdictions.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(raw)


def resolve(current_members: list[dict]) -> list[dict]:
    base = {j["code"]: dict(j) for j in load_base()}

    senators: dict[str, list[str]] = {}
    reps: dict[str, list[tuple[int, str]]] = {}
    for m in current_members:
        term = m["terms"][-1]
        st = term["state"]
        if st not in base:
            # A jurisdiction we have never seen: add it rather than drop it.
            base[st] = {"code": st, "name": st, "kind": "territory", "fips": None, "house_seats": 0}
        if term["type"] == "sen":
            senators.setdefault(st, []).append(m["id"]["bioguide"])
        else:
            reps.setdefault(st, []).append((int(term.get("district") or 0), m["id"]["bioguide"]))

    out = []
    for code, j in base.items():
        sens = sorted(senators.get(code, []))
        kind = "state" if sens else j.get("kind", "territory")
        seats = j.get("house_seats") or 0
        observed = len(reps.get(code, []))
        out.append(
            {
                "code": code,
                "name": j.get("name", code),
                "kind": kind,
                "fips": j.get("fips"),
                "house_seats": max(seats, observed),
                "senate_seats": 2 if kind == "state" else 0,
                "voting_representation": kind == "state",
                "senators": sens,
                "representatives": [b for _, b in sorted(reps.get(code, []))],
            }
        )
    out.sort(key=lambda j: (j["kind"] != "state", j["name"]))
    return out


def by_code(resolved: list[dict]) -> dict[str, dict]:
    return {j["code"]: j for j in resolved}
