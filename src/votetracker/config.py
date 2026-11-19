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

    # Re-download bill details older than this many days.
    bill_refresh_days: int = field(default_factory=lambda: int(_env("VT_BILL_REFRESH_DAYS", "7")))

    def r2_ready(self) -> bool:
        return all([self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key, self.r2_bucket])
