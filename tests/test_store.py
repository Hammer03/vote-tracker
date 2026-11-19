import io

from botocore.response import StreamingBody
from botocore.stub import Stubber

from votetracker.publish import Publisher
from votetracker.store import LocalStore, R2Store


def test_r2_store_get_put_and_missing():
    store = R2Store("acct123", "AKID", "SECRET", "vote-tracker-data")
    assert store.client.meta.endpoint_url == "https://acct123.r2.cloudflarestorage.com"
    with Stubber(store.client) as stub:
        stub.add_response(
            "put_object",
            {},
            {"Bucket": "vote-tracker-data", "Key": "v1/meta.json", "Body": b"{}",
             "ContentType": "application/json; charset=utf-8", "CacheControl": "public, max-age=60"},
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b'{"a":1}'), 7)},
            {"Bucket": "vote-tracker-data", "Key": "v1/meta.json"},
        )
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        store.put("v1/meta.json", b"{}", "application/json; charset=utf-8", "public, max-age=60")
        assert store.get_json("v1/meta.json") == {"a": 1}
        assert store.get("v1/missing.json") is None


def test_publisher_skips_unchanged(tmp_path):
    store = LocalStore(tmp_path)
    pub = Publisher(store)
    assert pub.publish_json("v1/a.json", {"x": 1}) is True
    pub.save_manifest()
    pub2 = Publisher(store)
    assert pub2.publish_json("v1/a.json", {"x": 1}) is False
    assert pub2.publish_json("v1/a.json", {"x": 2}) is True


def test_gz_bundles_roundtrip(tmp_path):
    store = LocalStore(tmp_path)
    rows = [{"id": "a", "n": 1}, {"id": "b", "n": 2}]
    store.put_jsonl_gz("state/x.jsonl.gz", rows)
    assert store.get_jsonl_gz("state/x.jsonl.gz") == rows
    assert store.get_jsonl_gz("state/missing.jsonl.gz") == []


def test_local_store_refuses_escape(tmp_path):
    store = LocalStore(tmp_path / "root")
    try:
        store.put("../outside.json", b"x", "text/plain")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_publisher_keeps_going_when_some_uploads_fail(tmp_path):
    class Flaky(LocalStore):
        def put(self, key, data, content_type, cache_control=None):
            if key.endswith("bad.json"):
                raise RuntimeError("503 Service Unavailable")
            super().put(key, data, content_type, cache_control)

    store = Flaky(tmp_path)
    pub = Publisher(store)
    pub.publish_json("v1/good.json", {"a": 1})
    pub.publish_json("v1/bad.json", {"b": 1})
    pub.save_manifest()
    assert pub.uploaded == 1
    assert pub.failed == ["v1/bad.json"]
    manifest = store.get_json("state/manifest.json")
    assert "v1/good.json" in manifest and "v1/bad.json" not in manifest
    # Next run retries only the failed file
    pub2 = Publisher(LocalStore(tmp_path))
    assert pub2.publish_json("v1/good.json", {"a": 1}) is False
    assert pub2.publish_json("v1/bad.json", {"b": 1}) is True


def test_r2_store_waits_out_a_503(monkeypatch):
    store = R2Store("acct123", "AKID", "SECRET", "vote-tracker-data")
    monkeypatch.setattr(R2Store, "OUTER_WAITS", (0,))
    with Stubber(store.client) as stub:
        stub.add_client_error("put_object", service_error_code="ServiceUnavailable", http_status_code=503)
        stub.add_response("put_object", {})
        store.put("v1/x.json", b"{}", "application/json")
        stub.assert_no_pending_responses()


def test_r2_store_does_not_retry_bad_credentials(monkeypatch):
    from botocore.exceptions import ClientError

    store = R2Store("acct123", "AKID", "SECRET", "vote-tracker-data")
    monkeypatch.setattr(R2Store, "OUTER_WAITS", (0,))
    with Stubber(store.client) as stub:
        stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
        try:
            store.put("v1/x.json", b"{}", "application/json")
        except ClientError:
            pass
        else:
            raise AssertionError("expected ClientError")
