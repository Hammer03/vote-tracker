"""Settings, read from environment variables.

Secrets are never stored in the code. In GitHub Actions they come from
repository secrets; locally you can export them in your shell.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


@dataclass
class Config:
    # api.data.gov key. Works for Congress.gov, OpenFEC, and GovInfo.
    congress_gov_api_key: str | None = field(default_factory=lambda: _env("CONGRESS_GOV_API_KEY"))

    # Cloudflare R2 (S3-compatible) storage.
    r2_account_id: str | None = field(default_factory=lambda: _env("R2_ACCOUNT_ID"))
    r2_access_key_id: str | None = field(default_factory=lambda: _env("R2_ACCESS_KEY_ID"))
    r2_secret_access_key: str | None = field(default_factory=lambda: _env("R2_SECRET_ACCESS_KEY"))
    r2_bucket: str | None = field(default_factory=lambda: _env("R2_BUCKET"))

    # Public address the apps read from, e.g. https://data.example.org
    public_base_url: str | None = field(default_factory=lambda: _env("PUBLIC_BASE_URL"))

    # Identify ourselves politely to government servers.
    user_agent: str = field(
        default_factory=lambda: _env(
            "VT_USER_AGENT",
            "Mozilla/5.0 (compatible; vote-tracker/0.1; +https://github.com/Hammer03/vote-tracker)",
        )
    )

    # Open States (state legislatures). Free key: openstates.org/accounts/signup
    openstates_api_key: str | None = field(default_factory=lambda: _env("OPENSTATES_API_KEY"))
    # Requests per day your key allows. Free keys: 250. Ask Plural Policy for
    # a higher tier (bronze 1,000 / silver 50,000) and raise this to match.
    openstates_daily_budget: int = field(default_factory=lambda: int(_env("OPENSTATES_DAILY_BUDGET", "240")))
    openstates_per_minute: int = field(default_factory=lambda: int(_env("OPENSTATES_PER_MINUTE", "10")))
    # How far back to start when a state is seen for the first time.
    openstates_backfill_days: int = field(default_factory=lambda: int(_env("OPENSTATES_BACKFILL_DAYS", "14")))

    # Firebase Cloud Messaging service account (the whole JSON file as text).
    firebase_service_account: str | None = field(default_factory=lambda: _env("FIREBASE_SERVICE_ACCOUNT"))

    # Re-download bill details older than this many days.
    bill_refresh_days: int = field(default_factory=lambda: int(_env("VT_BILL_REFRESH_DAYS", "7")))

    def r2_ready(self) -> bool:
        return all([self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key, self.r2_bucket])
