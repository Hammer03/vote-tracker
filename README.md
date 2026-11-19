# Vote Tracker — data backend

Free, open-source data for the Vote Tracker iPhone and Android apps.

It collects **official public records** — how every member of Congress
votes, what they vote on, and who they are — and publishes them as
small, pre-built JSON files. The apps just download those files. No user
accounts. No ads. No tracking.

**Rules for every record:** official sources only. Every side shown. No labels.
Every item links back to its official source so anyone can check it.

> Vote Tracker is independent. It is not affiliated with, endorsed by, or
> acting for any government entity.

## What's built

### Phase 1: Congress

| Data | Official source |
|---|---|
| House roll call votes | Office of the Clerk, U.S. House (XML) |
| Senate roll call votes | U.S. Senate Legislative Information System (XML) |
| Bill titles, sponsors, CRS summaries, full-text links | Congress.gov API (Library of Congress) |
| Members, contact info, district offices, social accounts, committees | congress-legislators (public domain) |
| Missed-vote % and votes-with-party % | Computed from the roll call records above |
| States, DC, territories | Data file — DC and Puerto Rico become states automatically once their senators appear in the official data |

### Phase 2: State legislatures (all 50 states, DC, Puerto Rico)

| Data | Source |
|---|---|
| Every current state legislator, with offices, phones, email, social | Open States nightly legislator files (public domain, no key) |
| State roll call votes, bills, summaries, full-text links | Open States API v3, collected from each legislature's official website |
| Missed votes, excused absences, votes-with-party | Computed from those roll calls, with the dates they cover |

Some legislatures publish only vote totals, not each member's vote. Those
votes are kept and marked `named_votes: false`.

### Following officials and alerts

Users follow any official in the app. Follows stay on the phone. The app
subscribes to a free Firebase Cloud Messaging topic for each one, and after
every run this backend sends one plain-fact alert per official with new
votes ("Rep. Jane Doe voted No — H.R. 1: On Passage — Passed"). Users can
pick every vote or major votes only. There is no user database.

Coming next: campaign money (FEC), lobbying (LDA.gov), floor speeches,
office spending, live election results, and city councils. See the project plan.

## How it works

```
GitHub Actions (every hour, free for public repos)
   └─ python -m votetracker run
        1. download only what's new from each official source
        2. rebuild the JSON files
        3. upload only files that changed → Cloudflare R2
Apps ── download JSON ──> R2 (behind Cloudflare's cache)
```

* When nothing new happened, a run makes a few small requests and uploads
  one file.
* Votes, bill data, and an upload manifest are saved under `state/` in the
  bucket, so each run picks up where the last one stopped.
* The apps never call government servers for this data. That keeps cost
  near $0 and spares the source sites.

The output files are described in [docs/DATA.md](docs/DATA.md).

## Setup

You need: a GitHub account, a Cloudflare account, and a free api.data.gov key.

### 1. Get a free api.data.gov key

Sign up at <https://api.data.gov/signup/>. The same key works for
Congress.gov, the FEC, and GovInfo.

### 2. Create the Cloudflare R2 bucket

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket**.
   Name it `vote-tracker-data`.
2. R2 → **Manage API tokens** → **Create API token**.
   Permission: **Object Read & Write**, limited to `vote-tracker-data`.
3. Copy the **Access Key ID**, **Secret Access Key**, and your **Account ID**.

### 3. Add GitHub secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
|---|---|
| `CONGRESS_GOV_API_KEY` | your api.data.gov key |
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | R2 token access key ID |
| `R2_SECRET_ACCESS_KEY` | R2 token secret |
| `R2_BUCKET` | `vote-tracker-data` |

### 4. Optional: state votes (Open States key)

Sign up free at <https://open.pluralpolicy.com/accounts/signup/>, confirm your
email, and copy the key from your profile page. Add it as the secret
`OPENSTATES_API_KEY`. Without it, state legislators still publish, but not
state votes.

Free keys allow 250 requests a day. The backend splits that across the day
and across states. For fresher state votes, ask Plural Policy for a higher
tier, then add repository **variables** to match:
`OPENSTATES_DAILY_BUDGET` (e.g. `1000`) and `OPENSTATES_PER_MINUTE` (e.g. `40`).

### 5. Optional: alerts (Firebase)

1. <https://console.firebase.google.com> → **Add project** (free plan).
2. Project settings → **Service accounts** → **Generate new private key**.
   A JSON file downloads.
3. Add a secret `FIREBASE_SERVICE_ACCOUNT` and paste the whole file's text.

Without it, alerts are planned and logged but not sent.

### 6. Run it the first time

**Actions → Update data → Run workflow.** The first run downloads every vote
in the current Congress and can take 30–60 minutes. After that it runs by
itself every hour and usually takes about a minute.

### 7. Make the files public (before launch)

The apps need a public web address for the bucket. In R2 → your bucket →
**Settings → Custom Domains**, connect a domain you own on Cloudflare
(for example `data.yourdomain.com`). That puts Cloudflare's free cache in
front of the files, which keeps reads inside the free tier. The `r2.dev`
address is rate-limited and only meant for testing.

Then add a repository **variable** `PUBLIC_BASE_URL` with that address.

> GitHub may pause scheduled workflows in a repo with no commits for 60
> days. The workflow tries to keep itself switched on. If it ever pauses,
> open **Actions → Update data** and click **Enable workflow**.

## Run it on your own computer

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                                          # tests use saved sample files
export CONGRESS_GOV_API_KEY=your-key               # optional
python -m votetracker run --store local --out build
```

Files land in `build/v1/`. Useful flags:

| Flag | What it does |
|---|---|
| `--no-fetch` | rebuild files from saved data, no downloads |
| `--no-bills` | skip Congress.gov lookups |
| `--max-new 5` | only grab a few new votes (quick test) |
| `--full-refresh` | re-download every vote this Congress |
| `--dry-run` | do everything except write files |
| `--states OH,PA` | only these state legislatures |
| `--no-states` | skip state legislatures |
| `--no-alerts` | don't send alerts |

## Costs

| Item | Cost |
|---|---|
| GitHub Actions (public repo) | free |
| Cloudflare R2: 10 GB storage, 10M reads/month | free |
| api.data.gov key | free |
| Open States key | free (250 requests/day; higher tiers on request) |
| Firebase Cloud Messaging | free |
| Custom domain for the cache | about the price of a domain per year |

## License

Code: MIT (see `LICENSE`). Data comes from U.S. government works (public
domain) and the public-domain congress-legislators project. Sources are
listed in every `meta.json`.
