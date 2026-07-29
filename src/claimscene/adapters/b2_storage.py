"""Real Backblaze B2 storage adapter (S3-compatible, via boto3).

B2 exposes an S3-compatible API, so any S3 client works. Credentials come
from the environment; nothing is hard-coded. ``boto3`` is imported lazily so
the package (and offline CI) does not require it. Ported from our other MIT
entry, Cinemory, which shares this B2 storage + provenance foundation.

The adapter keeps a **queryable run index**: every ``put`` appends a row
(key + size + content-type) to an ``index.jsonl`` object persisted *in the
bucket itself*. That makes the catalogue durable and multi-instance safe — a
fresh worker that never saw a write can still resolve a case another
instance sealed (see :meth:`reload_index`). The index mirrors
``InMemoryStorage.index`` exactly, so offline and live paths expose the
identical provenance surface. Persisting it (:meth:`_persist_index`) is a
merge-on-write: every write re-reads the remote index and unions rows *by
key* rather than overwriting it from the local snapshot, so two independent
writers (a request thread and a background render-job thread, or two
instances) never clobber each other's rows — see that method's docstring,
and :meth:`delete`'s ``removed`` handling for why a deletion needs its own
merge path.

Env (canonical Backblaze names primary; legacy aliases also accepted —
resolution lives in :func:`claimscene.config.resolve_b2_config`):
  B2_BUCKET_NAME          target bucket
  B2_S3_ENDPOINT          e.g. https://s3.eu-central-003.backblazeb2.com
                          (alias: B2_ENDPOINT_URL; a missing scheme is added)
  B2_APPLICATION_KEY_ID   application key id  (alias: B2_KEY_ID)
  B2_APPLICATION_KEY      application key     (alias: B2_APP_KEY)
"""
from __future__ import annotations

import json

# Object holding the run index (JSONL catalogue), relative to the key prefix.
_INDEX_NAME = "index.jsonl"

# Fallback region for SigV4 signing when none is configured or derivable. B2
# always encodes the real region in the endpoint host, so this only ever
# applies to a hand-rolled non-B2 endpoint; SigV4 still needs *some* region.
_DEFAULT_REGION = "us-east-1"


def region_from_endpoint(endpoint_url: str | None) -> str | None:
    """Derive the S3 region from a B2/S3 endpoint host.

    B2 endpoints look like ``s3.eu-central-003.backblazeb2.com`` — the region
    is the second dotted label. Returns ``None`` when it cannot be inferred.
    """
    if not endpoint_url:
        return None
    host = endpoint_url.split("://", 1)[-1].split("/", 1)[0]
    parts = host.split(".")
    if len(parts) >= 3 and parts[0] in ("s3", "s3-api"):
        return parts[1] or None
    return None


def build_b2_client(
    *, endpoint_url: str, region: str, key_id: str, app_key: str
) -> object:
    """Construct a boto3 S3 client wired for Backblaze B2 presigned URLs.

    The critical detail (ported from Cinemory PR #17/#19): B2 rejects a
    presigned GET unless the client signs with **SigV4** and the **correct
    region**. boto3's defaults sign some requests with a region/algorithm B2
    refuses, so a naive client 401s the moment ``/cases/{id}/schematic`` or
    ``/cases/{id}/illustration`` redirects a browser to a presigned URL. We
    pin ``signature_version="s3v4"`` and an explicit ``region_name`` so the
    302-presign playback path actually works live.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


class B2Storage:
    def __init__(
        self,
        bucket: str | None = None,
        *,
        endpoint_url: str | None = None,
        key_id: str | None = None,
        app_key: str | None = None,
        client: object | None = None,
    ) -> None:
        # Lazy import avoids import-time coupling with config (which imports
        # the adapters package).
        from ..config import resolve_b2_config

        cfg = resolve_b2_config()
        self.bucket = bucket or cfg.bucket
        self.endpoint_url = endpoint_url or cfg.endpoint_url
        access_key_id = key_id or cfg.key_id
        secret_access_key = app_key or cfg.app_key

        prefix = cfg.key_prefix or ""
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        self.key_prefix = prefix
        self._index_key = f"{self.key_prefix}{_INDEX_NAME}"

        if not self.bucket:
            raise RuntimeError("B2 bucket not configured (set B2_BUCKET_NAME)")
        if not self.endpoint_url:
            raise RuntimeError(
                "B2 endpoint not configured (set B2_S3_ENDPOINT or B2_ENDPOINT_URL)"
            )

        # Region for SigV4 signing: explicit B2_REGION wins, else derive it
        # from the endpoint host, else the safe SigV4 default.
        self.region = (
            cfg.region or region_from_endpoint(self.endpoint_url) or _DEFAULT_REGION
        )

        # ``client`` is a test seam (inject an S3-compatible stub); the real
        # path builds a boto3 client — the only branch needing boto3 + creds.
        if client is not None:
            self._client = client
        else:  # pragma: no cover - real-path only (needs boto3 + live creds)
            if not access_key_id or not secret_access_key:
                raise RuntimeError(
                    "B2 credentials not configured (set B2_APPLICATION_KEY_ID/"
                    "B2_APPLICATION_KEY or B2_KEY_ID/B2_APP_KEY)"
                )
            try:
                self._client = build_b2_client(
                    endpoint_url=self.endpoint_url,
                    region=self.region,
                    key_id=access_key_id,
                    app_key=secret_access_key,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for B2Storage: pip install claimscene[live]"
                ) from exc

        # Seed the in-memory index from whatever is already durable in the
        # bucket, so a fresh instance inherits the full catalogue.
        self.index: list[dict] = self.reload_index()

    # ── run index (queryable catalogue, mirrors InMemoryStorage.index) ────────
    def index_jsonl(self) -> str:
        return "\n".join(json.dumps(row, sort_keys=True) for row in self.index)

    def reload_index(self) -> list[dict]:
        """Re-read ``index.jsonl`` from the bucket (query-time freshness).

        Best-effort: a missing index (first run) or any read error yields an
        empty catalogue rather than raising, so lookups degrade to
        "not found" instead of crashing.
        """
        self.index = self._read_remote_index_rows()
        return self.index

    def _read_remote_index_rows(self) -> list[dict]:
        """Current durable ``index.jsonl`` rows — best-effort by design.

        A missing index (first run), a read error, a corrupt line, or a
        non-row line yields FEWER rows rather than raising: readers degrade
        to "not found", and because :meth:`_persist_index` calls this on
        every put, the next merge-on-write rewrites a clean index
        (self-healing) — a corrupt remote line must never be able to fail
        ``put``. Ported from Cinemory's identical adapter method.
        """
        try:
            raw = self._client.get_object(Bucket=self.bucket, Key=self._index_key)[
                "Body"
            ].read()
        except Exception:  # missing/unreadable index (first run) → empty
            return []
        rows: list[dict] = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:  # corrupt line — drop; next persist writes clean
                continue
            if isinstance(row, dict) and "key" in row:
                rows.append(row)
        return rows

    def _persist_index(self, *, removed: str | None = None) -> None:
        """Merge-on-write: union our rows with the current remote index.

        Concurrent writers (e.g. a request thread and a background render-job
        thread — see ``claimscene.api._job_storage``) used to last-writer-wins
        clobber each other's ``index.jsonl`` rows: a plain overwrite of the
        local snapshot could erase another writer's rows entirely. Instead of
        writing our snapshot blindly, we re-read the remote index and union
        rows **by key** — idempotent, and newest-wins per key (our in-memory
        row, which includes the put that triggered this call, overlays the
        remote row for the same key; keys are content-addressed, so same-key
        rows are identical in practice). A small read-modify-write race window
        remains between the re-read and the put; that is accepted — losing it
        costs one index row until the next merge-on-write or manual reconcile,
        and building distributed locking over B2 is out of scope by design.
        Ported from Cinemory's identical adapter method (``#19``/``#36``
        there).

        ``removed``: set by :meth:`delete` to the key just deleted. The union
        above is correct for a PUT (a key can only ever gain/keep a row), but
        is WRONG for a DELETE: the remote snapshot we just re-read still
        lists the key we are trying to remove, so a plain union would
        silently resurrect its row in the merged result. Dropping it from
        the remote snapshot BEFORE the union closes that hole — our
        in-memory ``index`` (which no longer has the row either, see
        :meth:`delete`) can never add it back.
        """
        merged: dict[str, dict] = {row["key"]: row for row in self._read_remote_index_rows()}
        if removed is not None:
            merged.pop(removed, None)
        merged.update((row["key"], row) for row in self.index)
        self.index = list(merged.values())
        self._client.put_object(
            Bucket=self.bucket,
            Key=self._index_key,
            Body=self.index_jsonl().encode("utf-8"),
            ContentType="application/x-ndjson",
        )

    # ── object I/O ────────────────────────────────────────────────────────────
    def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str:
        actual_key = f"{self.key_prefix}{key}"
        self._client.put_object(
            Bucket=self.bucket, Key=actual_key, Body=data, ContentType=content_type
        )
        # Record the *logical* key (pre-prefix), matching InMemoryStorage, so
        # callers can look a case up and fetch it back via ``get(key)``.
        self.index.append({"key": key, "size": len(data), "content_type": content_type})
        self._persist_index()
        host = self.endpoint_url.replace("https://", "").rstrip("/")
        return f"https://{self.bucket}.{host}/{actual_key}"

    def get(self, key: str) -> bytes:
        actual_key = f"{self.key_prefix}{key}"
        return self._client.get_object(Bucket=self.bucket, Key=actual_key)["Body"].read()

    def delete(self, key: str) -> None:
        """Delete the object at ``key`` and drop its durable index row.

        Deleting an absent key is a no-op (S3/B2 ``DeleteObject`` is itself
        idempotent — it does not error on a missing key), so callers never
        need to check ``exists`` first. See :meth:`_persist_index`'s
        ``removed`` parameter for why this needs its own merge path rather
        than the plain union :meth:`put` uses. Ported from Cinemory's
        identical adapter method (added there for its own ``DELETE
        /me/data``).

        Known limitation (same family as the accepted race documented in
        :meth:`_persist_index`, not something ``removed`` fixes): a SECOND,
        already-constructed ``B2Storage`` instance holding an in-memory
        ``index`` snapshot taken BEFORE this delete still carries the
        deleted row. If that instance later calls :meth:`put` for anything
        at all — before ever calling :meth:`reload_index` — its own
        merge-on-write union re-adds the deleted row to the durable index,
        because ``self.index`` (not just the remote re-read) is part of
        that union. Multi-instance deployments therefore have a window
        where a stale writer can resurrect a just-deleted tenant's row;
        this mirrors the codebase's existing accepted stance that
        distributed locking over B2 is out of scope, so it is documented
        rather than solved here.
        """
        actual_key = f"{self.key_prefix}{key}"
        self._client.delete_object(Bucket=self.bucket, Key=actual_key)
        self.index = [row for row in self.index if row["key"] != key]
        self._persist_index(removed=key)

    def get_url(self, key: str, *, expires_in: int = 3600) -> str:
        """Mint a fresh time-limited presigned GET URL (private bucket).

        The presigned URL is never persisted — manifests keep the canonical
        storage URL and hashes. Signing is local (no network round-trip).
        """
        actual_key = f"{self.key_prefix}{key}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": actual_key},
            ExpiresIn=expires_in,
        )

    def exists(self, key: str) -> bool:  # pragma: no cover - real-path only
        from botocore.exceptions import ClientError

        actual_key = f"{self.key_prefix}{key}"
        try:
            self._client.head_object(Bucket=self.bucket, Key=actual_key)
            return True
        except ClientError:
            return False
