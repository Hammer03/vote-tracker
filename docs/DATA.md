# Data files (schema v1)

All public files live under `v1/` at the bucket's public address. They are
plain JSON (UTF-8). The apps download them; nothing else is needed.

Paths below are relative to `v1/`.

## Start here

### `meta.json`
Refreshed every run.

```json
{
  "schema_version": 1,
  "generated_at": "2026-11-19T01:24:31+00:00",
  "latest_vote_at": "2026-11-18T17:14:00-05:00",
  "congress": 119,
  "counts": {"members": 539, "house_votes": 700, "senate_votes": 600, "bills": 450},
  "capitol_switchboard": "(202) 224-3121",
  "disclaimer": "Vote Tracker is an independent app. ...",
  "sources": [{"name": "...", "url": "..."}]
}
```

Show `disclaimer` and `sources` in the app (Google Play requires official
source links and a "not affiliated with the government" disclaimer).

## Finding a user's officials

### `jurisdictions.json`
All 50 states, DC, and the territories.

| Field | Meaning |
|---|---|
| `code`, `name`, `fips` | e.g. `OH`, `Ohio`, `39` (FIPS matches Census Geocoder results) |
| `kind` | `state`, `district`, or `territory`. Becomes `state` automatically when senators appear in the official data. |
| `voting_representation` | `true` for states |
| `house_seats`, `senate_seats` | seat counts |
| `senators`, `representatives` | Bioguide IDs |

### `states/{CODE}.json`
Everything for one state: `senators` (member cards) and `house`, a list of
`{district, member, vacant}`. District `0` means at-large or a delegate seat.

**Address → district:** the phone can turn an address or location into a
congressional district with the U.S. Census Geocoder, then open this file.
The address never touches our servers.

## Members

### `members/index.json`
`{"members": [card, ...]}` — every sitting member. A **card** is:

```json
{"bioguide": "J000289", "name": "Jim Jordan", "last_name": "Jordan",
 "party": "Republican", "party_code": "R", "chamber": "house",
 "title": "Representative", "state": "OH", "district": 4,
 "votes_on_floor": true, "photo_url": "https://..."}
```

`title` is `Senator`, `Representative`, `Delegate`, or `Resident Commissioner`.
`votes_on_floor` is `false` for delegates (they can't vote on final passage).

### `members/{bioguide}.json`
The card plus:

| Field | Meaning |
|---|---|
| `term` | current term start/end |
| `in_chamber_since`, `in_congress_since`, `years_in_congress`, `terms_served` | time in office |
| `caucus` | party an Independent caucuses with, if any |
| `contact` | DC phone, office, address, website, contact form, Capitol switchboard |
| `district_offices` | local offices with phone numbers |
| `social` | official social accounts |
| `committees` | committee and subcommittee assignments, with titles |
| `stats` | this Congress (see below) |
| `recent_votes` | last 20 roll calls with this member's position |
| `ids` | cross-walk IDs (FEC, GovTrack, Vote Smart, ...) for later phases |
| `sources` | where this profile came from |

`stats`:

| Field | Meaning |
|---|---|
| `roll_calls` | roll calls where this member appears |
| `missed`, `missed_pct` | recorded as "Not Voting" |
| `present` | voted "Present" |
| `with_party`, `with_party_eligible`, `with_party_pct` | Of the Yea/Nay votes where the member's party did not split evenly, how often the member voted with most of the party. Independents are compared with the party they caucus with. |

### `members/{bioguide}/votes-{congress}.json`
Every roll call this Congress, newest first, with `position`
(`yea`, `nay`, `present`, `not_voting`, `other`) and `position_raw`
(the exact official text, e.g. `Aye`).

## Votes

### `votes/latest.json`
The 100 most recent roll calls, both chambers. Each row:

```json
{"id": "house-119-1-100", "chamber": "house", "congress": 119, "session": 1,
 "number": 100, "date": "2025-04-09", "datetime": "2025-04-09T17:14:00-04:00",
 "question": "On Motion to Suspend the Rules and Pass",
 "description": "Prioritizing Veterans Survivors Act",
 "result": "Passed", "required": "2/3",
 "bill": {"id": "119-hr-1228", "label": "H.R. 1228"},
 "nomination": null, "amendment": null,
 "totals": {"yea": 2, "nay": 2, "present": 0, "not_voting": 1, "other": 0},
 "path": "votes/house/119-1/100.json"}
```

### `votes/{chamber}/{congress}-{session}.json`
Same rows for one session, newest first.

### `votes/{chamber}/{congress}-{session}/{number}.json`
One roll call in full: everything above plus `positions` (every member's
vote with `bioguide`, `name`, `party`, `state`, `vote`, `raw`),
`party_totals`, `bill.title`, `amendment`, `nomination`, `tie_breaker`
(Senate), and `source` (`url` = official page, `data_url` = official XML).

`required` is the majority needed: `1/2`, `3/5`, `2/3`, or `null` (quorum calls).

## Bills

### `bills/{congress}/{type}{number}.json`
For every bill that had a roll call vote. Example: `bills/119/hr1228.json`.

| Field | Meaning |
|---|---|
| `label`, `title` | `H.R. 1228`, official title |
| `sponsors`, `cosponsors_count` | who introduced it |
| `introduced_date`, `latest_action`, `became_law`, `laws` | status |
| `policy_area` | Congress.gov subject |
| `summary` | latest Congressional Research Service summary (`text`, `action`, `action_date`) |
| `text_versions` | every version with `pdf` / `html` / `xml` links |
| `full_text_url` | latest full text (for the "Full bill" button) |
| `url` | Congress.gov page |
| `roll_call_votes` | every roll call on this bill (rows as in `latest.json`) |

`type` is one of `hr`, `s`, `hres`, `sres`, `hjres`, `sjres`, `hconres`, `sconres`.

## Caching

| Files | Cache |
|---|---|
| `meta.json` | 1 minute |
| `votes/latest.json` | 2 minutes |
| single votes, bills | 1 hour |
| everything else | 5 minutes |

## Internal files

`state/...` holds the pipeline's working data (vote bundles, bill cache,
upload manifest, last run report). Apps should not read it.
