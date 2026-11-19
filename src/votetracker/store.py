"""Where files live: a local folder (development) or Cloudflare R2 (production).

Both stores hold two kinds of keys:

* ``v1/...``    public JSON files the apps download
* ``state/...`` the pipeline's own working data (vote bundles, caches,
                manifest). Also public-record data, just not meant for apps.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


def dumps(obj) -> bytes:
    """Compact, stable JSON (sorted keys keep diffs and hashes stable)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


class Store:
    def get(self, key: str) -> bytes | None:  # pragma: no cover - interface
        raise NotImplementedError

    def put(self, key: str, data: bytes, content_type: str, cache_control: str | None = None) -> None:  # pragma: no cover
        raise NotImplementedError

    # --- helpers -----------------------------------------------------------

    def get_json(self, key: str, default=None):
        raw = self.get(key)
        if raw is None:
            return default
        return json.loads(raw)

    def put_json(self, key: str, obj, cache_control: str | None = None) -> None:
        self.put(key, dumps(obj), "application/json; charset=utf-8", cache_control)

    def get_jsonl_gz(self, key: str) -> list[dict]:
        raw = self.get(key)
        if raw is None:
            return []
        text = gzip.decompress(raw).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def put_jsonl_gz(self, key: str, rows: list[dict]) -> None:
        body = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for r in rows)
        self.put(key, gzip.compress(body.encode("utf-8"), mtime=0), "application/gzip", "no-store")

    def get_json_gz(self, key: str, default=None):
        raw = self.get(key)
        if raw is None:
            return default
        return json.loads(gzip.decompress(raw))

    def put_json_gz(self, key: str, obj) -> None:
        self.put(key, gzip.compress(dumps(obj), mtime=0), "application/gzip", "no-store")


class LocalStore(Store):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError(f"key escapes store root: {key}")
        return p

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.exists() else None

    def put(self, key: str, data: bytes, content_type: str, cache_control: str | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class R2Store(Store):
    """Cloudflare R2 through its S3-compatible API."""

    def __init__(self, account_id: str, access_key_id: str, secret_access_key: str, bucket: str):
        import boto3
        from botocore.config import Config as BotoConfig

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=BotoConfig(retries={"max_attempts": 8, "mode": "adaptive"}, max_pool_connections=16),
        )

    # R2 has short regional outages (HTTP 503). boto3 already retries a few
    # times within seconds; this outer loop waits longer before giving up.
    OUTER_WAITS = (5, 15, 45)

    def _with_retry(self, fn):
        for wait in (*self.OUTER_WAITS, None):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                if wait is None or not _transient(exc):
                    raise
                log.warning("R2 busy (%s); retrying in %ss", exc.__class__.__name__, wait)
                time.sleep(wait)

    def get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            obj = self._with_retry(lambda: self.client.get_object(Bucket=self.bucket, Key=key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return obj["Body"].read()

    def put(self, key: str, data: bytes, content_type: str, cache_control: str | None = None) -> None:
        extra = {"ContentType": content_type}
        if cache_control:
            extra["CacheControl"] = cache_control
        self._with_retry(lambda: self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra))


def _transient(exc: Exception) -> bool:
    """Server-side or network errors worth waiting out (not 'no such key' or bad credentials)."""
    resp = getattr(exc, "response", None) or {}
    code = (resp.get("Error") or {}).get("Code", "")
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0)
    if code in ("NoSuchKey", "404", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
        return False
    if status >= 500 or code in ("ServiceUnavailable", "InternalError", "SlowDown", "RequestTimeout"):
        return True
    return exc.__class__.__name__ in ("EndpointConnectionError", "ConnectionClosedError", "ReadTimeoutError",
                                       "ConnectTimeoutError")
