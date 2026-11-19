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

Senate votes taken from Voteview (when senate.gov blocks the backend) have
`source.via = "voteview"`, a date but no time, and `required: null`.
Paired and announced positions are recorded as `not_voting`, matching the
Senate's own record, with the announcement kept in `raw`.

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

## Campaign money (Congress)

Built once a day from the FEC's bulk data files. Individual donors are never
named — only totals. (FEC rules forbid using individual contributor
information for commercial purposes or to solicit donations.)

### `money/{bioguide}.json`

| Field | Meaning |
|---|---|
| `fec_candidate_id`, `fec_url` | the campaign on fec.gov |
| `periods` | `election_before_term` (e.g. `2024`) and `this_term` (e.g. `[2026]`) two-year cycles |
| `cash.start_of_term` | cash on hand at the end of the election that won this term: `{amount, as_of}` |
| `cash.latest` | latest reported cash on hand: `{amount, as_of}` |
| `cash.change` | latest minus start of term |
| `this_term` | totals during this term: `receipts`, `disbursements`, `from_individuals`, `from_pacs_and_other_committees`, `from_parties`, `from_candidate`, `loans_from_candidate` |
| `by_cycle` | the same figures for each cycle, plus `cash_start`, `cash_end`, `debts`, `coverage_end` |
| `committee_contributions.this_term` / `.election_before_term` | every committee that gave directly: `committee_id`, `name`, `kind` (PAC, Super PAC, Hybrid PAC, Party committee, Candidate committee, ...), `connected_org`, `org_type` (Corporation, Labor organization, Trade association, ...), `party`, `direct`, `path` |
| `outside_spending.this_term` / `.election_before_term` | money spent independently: `support` and `oppose` amounts by committee |
| `notes` | plain-language notes to show with the numbers |

Rows are sorted by amount, largest first. Show `as_of` dates with cash figures.

### `money/committees/{committee_id}.json`
One committee's money to every sitting member during each member's current
term: `members` (member card + `direct`, `support`, `oppose`), and
`totals_to_current_members`. Lets users look up any PAC and see everyone it
gave to or spent for or against.

### `money/committees/index.json`
Every committee that appears above, with totals, for search.

Member profiles (`members/{bioguide}.json`) also carry a short `money`
summary: `cash_latest`, `cash_start_of_term`, `raised_this_term`, `path`.

## State legislatures

### `state-legislatures.json`
One row per legislature (50 states, DC, PR): `state`, `name`, `legislators`,
`sessions`, and `coverage` (see below).

### `states/{CODE}/legislature.json`
| Field | Meaning |
|---|---|
| `sessions` | current session(s): `identifier`, `name`, `start_date`, `end_date` |
| `chambers` | `upper`, `lower`, or `legislature` (Nebraska, DC). Each has `name`, `seats`, `members`, and `districts`: `[{district, members: [card]}]`. Some districts elect more than one member. |
| `coverage` | `roll_calls` we have, `with_names` (how many list each member's vote), `from` / `to` dates, `last_checked` |

A state legislator **card**:
```json
{"id": "11111111-aaaa-...", "name": "Carol Test", "party": "Republican", "party_code": "R",
 "title": "State Representative", "chamber": "lower", "state": "OH", "district": "40",
 "image": "https://..."}
```
`title` follows each state's own usage (State Senator, State Representative,
Assembly Member, Delegate, Councilmember).

### `legislators/{id}.json`
The card plus `email`, `offices` (`capitol` / `district` with address and
phone), `links`, `social`, `birth_date`, `biography`, `stats`,
`recent_votes` (last 20), and `sources`.

`stats` matches Congress (`roll_calls`, `missed`, `missed_pct`, `excused`,
`present`, `with_party`, `with_party_pct`) plus `covers_from` / `covers_to`.
**Always show the dates** — state records start when this backend began
collecting, not at the start of the member's career. Excused absences are
counted apart from missed votes.

### `legislators/{id}/votes.json`
Every roll call we have for this legislator, newest first (same row format as
Congress member histories).

### `state-votes/{CODE}/latest.json`, `state-votes/{CODE}/{session}.json`
Vote lists for a state.

### `state-votes/{CODE}/{session}/{uuid}.json`
One roll call: `question`, `motion_types` (e.g. `passage`), `result`,
`chamber`, `date`, `bill` (`label`, `title`), `positions` (each member:
`person`, `name`, `party`, `vote`, `raw`), `totals`, `official_counts`,
`named_votes`, and `source` (`url` = the legislature's own page when known).

### `state-bills/{CODE}/{session}/{id}.json`
`label` (e.g. `HB 101`), `title`, `abstracts`, `sponsors`, `subjects`,
`latest_action`, `versions` (every version with links), `full_text_url`,
`sources`, `openstates_url`, and `roll_call_votes`.

**Finding state legislators for an address:** the Census Geocoder also returns
state legislative districts (upper and lower). Match them to `district` in
`legislature.json`.

## Following and alerts

Follows live only on the phone. To get alerts, the app subscribes to
Firebase Cloud Messaging topics (formats are also in `meta.json` → `alerts`):

| Topic | Gets |
|---|---|
| `m-{bioguide}` | every new roll call by that member of Congress |
| `m-{bioguide}-major` | final votes only: passage, adoption, concurrence, conference reports, veto overrides, nominations, treaties, impeachment |
| `s-{id}` | every new roll call by that state legislator |
| `s-{id}-major` | passage and veto-override votes only |

After each run the backend sends one message per official with new votes
from the last 3 days (older votes found while catching up never alert).

```json
{"notification": {"title": "Rep. Jim Jordan voted Yes",
                  "body": "H.R. 1228: On Motion to Suspend the Rules and Pass — Passed"},
 "data": {"type": "votes", "level": "federal", "member": "J000289",
          "path": "members/J000289.json", "votes": "house-119-1-100"}}
```

`data.path` is the profile file to open when the alert is tapped.

Suggested settings per followed official:
- **All votes** → subscribe to the plain topic only.
- **Major votes only** → subscribe to the `-major` topic only.
- **Off** → no topic (still followed in the app).

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
