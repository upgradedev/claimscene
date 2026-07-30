"""Real Genblaze-backed generative-media provider (clips via GMI Cloud).

Wraps a Genblaze single-step ``Pipeline`` behind the :class:`MediaProvider`
port. Supports two modalities:

  * ``modality="image"``: a text-to-image still. Not called by
    ``pipeline.py`` any more: the illustration clip's seed image is now the
    case's own deterministic schematic raster, not a generated still (see
    ``pipeline.py`` step 5). Kept working here (and still contract-tested in
    ``test_genblaze_contract.py``) in case a real generated-still path is
    ever added back.
  * ``modality="video"`` — the illustration clip (``pixverse-v6-i2v`` by
    default; ``Kling-Image2Video-V2.1-Master`` as the premium option). The
    only operation ClaimScene's pipeline calls today.

Genblaze is imported lazily so the offline package and CI depend on neither
it nor any provider key. Ported from our other MIT entry, Cinemory, which
shares this adapter foundation; shapes verified against the pinned SDK
(``genblaze-core`` 0.3.6, ``genblaze-s3`` 0.3.5, ``genblaze-gmicloud``
0.3.3 — what the ``genblaze`` 0.4.x metapackage resolves):

* ``Pipeline(name).step(provider, model=, prompt=, modality=,
  external_inputs=, **params).run(sink=, timeout=, raise_on_failure=True)
  -> PipelineResult``
* ``external_inputs`` is the SDK's only mechanism for caller-held input
  media: it seeds ``Step.inputs`` with ``Asset`` objects whose **HTTPS
  URLs** the provider's ``input_mapping`` routes into native payload slots
  (pixverse / Kling I2V: ``image``). Raw bytes and ``data:`` URIs are
  rejected by the SDK's SSRF gate. The step the provider receives MUST
  carry those Assets — building the step without ``external_inputs=`` is
  the silent live-path bug the contract tests pin (GMI answers
  ``image (Required parameter is missing)``).
* ``result.run.steps[-1].assets[0]`` is a URL-addressed ``Asset`` (``url``,
  ``sha256``, ``size_bytes``, ``media_type``); bytes come from durable
  storage or the hosted URL, then the sealed sha256 is re-verified.

Input hosting has two routes:

1. **Chained output** — when the input bytes are an asset this provider just
   generated (the establish-shot still feeding the i2v clip), its hosted
   output URL is reused. With a storage backend attached, Genblaze's sink has
   already rewritten that output URL to the backend's DURABLE object URL —
   and for a private bucket that URL needs auth, so the provider's queue
   worker fetching it unauthenticated gets a 401 and GMICloud rejects the i2v
   submit with a 400. The chained URL is therefore re-presigned to a
   short-lived GET the worker can actually fetch (probe-verified: a private
   B2 durable URL 400s the submit; the same object via a presigned URL
   generates). With no backend, the provider-hosted output URL is already
   public and is reused as-is.
2. **Storage backend** — otherwise the bytes are persisted through the
   Genblaze storage backend (Backblaze B2 via ``genblaze_s3``) under a
   content-addressed key and handed over as a short-lived presigned URL.

With a backend attached, Genblaze's ``ObjectStorageSink`` also persists every
generated asset to B2 and seals a SHA-256 provenance manifest, which the
pipeline chains into ClaimScene's own case manifest.

GMI quirk (probe-verified): the request-queue submit POST may BLOCK for
30-60s and return success synchronously. The SDK's default HTTP timeout
(120s) covers it; never lower it below 90s.

Env:
  GENBLAZE_PROVIDER   provider family (default ``gmicloud``)
  GMI_API_KEY         GMI Cloud credential
  B2_BUCKET_NAME (+ B2 creds) optional — enables input hosting + durable sink
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.request
from collections.abc import Callable
from typing import Any

# Matches genblaze AssetTransfer's default download cap (5 GiB).
_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024 * 1024

_MODALITY_NAMES = {"image": "IMAGE", "video": "VIDEO"}

#: Sentinel: "no backend argument given" (distinct from an explicit ``None``,
#: which means "run without any storage backend / sink").
_UNSET = object()

#: Positive magic-byte sniffs for the image formats the pipeline produces.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)
_MEDIA_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _image_media_type(data: bytes) -> str:
    """Best-effort MIME sniff for an input image (default ``image/png``)."""
    for magic, media_type in _IMAGE_MAGIC:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _https_download(url: str, *, timeout: float = 180.0) -> bytes:  # pragma: no cover - network
    """Fetch an asset's hosted bytes. HTTPS-only (credentials never in URLs)."""
    if not url.lower().startswith("https://"):
        raise ValueError(f"refusing to download non-HTTPS asset URL: {url!r}")
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - scheme checked
        data = resp.read(_MAX_DOWNLOAD_BYTES + 1)
    if len(data) > _MAX_DOWNLOAD_BYTES:
        raise ValueError(f"asset exceeds {_MAX_DOWNLOAD_BYTES}-byte download cap: {url!r}")
    return data


class GenblazeMediaProvider:
    name = "genblaze"

    #: Content-addressed namespace for hosted step inputs, separate from the
    #: sink's generated-asset layout.
    _INPUT_KEY_PREFIX = "chain-inputs"
    #: Presigned-URL lifetime for the provider's server-side input fetch.
    _INPUT_URL_EXPIRES_SECS = 3600

    def __init__(
        self,
        provider: str | None = None,
        *,
        image_provider_obj: Any | None = None,
        video_provider_obj: Any | None = None,
        backend: Any = _UNSET,
        bucket: str | None = None,
        downloader: Callable[[str], bytes] | None = None,
    ) -> None:
        """
        Args:
            provider: Genblaze provider family (default ``gmicloud``).
            image_provider_obj / video_provider_obj: already-constructed
                Genblaze provider instances. When supplied, credential-bound
                resolution is bypassed — used by the SDK-boundary contract
                tests to inject the SDK's own mock provider.
            backend: an already-constructed Genblaze ``StorageBackend``
                (bypasses live B2 construction), or an explicit ``None`` to
                run with NO backend/sink even when B2 env vars are present
                (generated assets are then read from their hosted URLs).
                When omitted entirely, the backend resolves lazily from the
                B2 environment.
            bucket: B2 bucket name (falls back to ``B2_BUCKET_NAME``).
            downloader: byte-fetch seam for the hosted-URL path (injectable
                for tests); defaults to a HTTPS-only GET.
        """
        self.provider_name = provider or os.environ.get("GENBLAZE_PROVIDER", "gmicloud")
        self._provider_objs = {"image": image_provider_obj, "video": video_provider_obj}
        self._backend = None if backend is _UNSET else backend
        self._backend_resolved = backend is not _UNSET
        self._bucket = bucket or os.environ.get("B2_BUCKET_NAME")
        self._download = downloader or _https_download
        #: The sealed Genblaze manifest from the most recent ``generate`` call.
        self.last_manifest: Any | None = None
        #: Hosted URL of the most recently generated asset (chaining seam).
        self.last_asset_url: str | None = None
        #: sha256(bytes) → hosted URL for every asset this instance generated,
        #: so a generated still can feed the clip step with zero self-hosting.
        self._hosted_by_sha: dict[str, str] = {}

    # ── provider / storage wiring (credential-bound; resolved lazily) ────────
    def _resolve_provider(self, modality: str) -> Any:
        if modality not in _MODALITY_NAMES:
            raise ValueError(f"unsupported modality {modality!r} (image|video)")
        injected = self._provider_objs.get(modality)
        if injected is not None:
            return injected
        return self._real_provider(modality)

    def _real_provider(self, modality: str) -> Any:  # pragma: no cover - GMI SDK + key
        if self.provider_name == "gmicloud":
            from genblaze_gmicloud import (  # type: ignore
                GMICloudImageProvider,
                GMICloudVideoProvider,
            )

            cls = {"image": GMICloudImageProvider,
                   "video": GMICloudVideoProvider}[modality]
            return cls()
        raise NotImplementedError(f"provider {self.provider_name!r} not wired yet")

    def _resolve_backend(self) -> Any | None:
        if self._backend_resolved:
            return self._backend
        self._backend = self._real_backend()
        self._backend_resolved = True
        return self._backend

    def _real_backend(self) -> Any | None:  # pragma: no cover - needs B2 creds
        if not self._bucket:
            return None
        try:
            from genblaze_s3 import S3StorageBackend  # type: ignore
        except ImportError:
            return None

        from ..config import resolve_b2_config

        cfg = resolve_b2_config()
        if not (cfg.key_id and cfg.app_key):
            return None
        region = None
        if cfg.endpoint_url:
            match = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", cfg.endpoint_url)
            if match:
                region = match.group(1)
        return S3StorageBackend.for_backblaze(
            self._bucket, region=region, key_id=cfg.key_id, app_key=cfg.app_key,
        )

    # ── generation ────────────────────────────────────────────────────────────
    def generate(
        self,
        *,
        model: str,
        prompt: str,
        modality: str = "video",
        inputs: list[bytes] | None = None,
        params: dict | None = None,
    ) -> bytes:
        from genblaze_core import Modality as GbModality  # type: ignore
        from genblaze_core import ObjectStorageSink, Pipeline

        provider = self._resolve_provider(modality)
        gb_modality = getattr(GbModality, _MODALITY_NAMES[modality])
        backend = self._resolve_backend()
        pipeline = Pipeline("claimscene-step").step(
            provider,
            model=model,
            prompt=prompt,
            modality=gb_modality,
            external_inputs=self._external_inputs(inputs, backend),
            **(params or {}),
        )
        sink = ObjectStorageSink(backend) if backend is not None else None
        result = pipeline.run(sink=sink, timeout=600, raise_on_failure=True)

        # Genblaze sealed a provenance manifest for this run — surface it so
        # the case pipeline can chain it into ClaimScene's own manifest.
        self.last_manifest = result.manifest

        asset = result.run.steps[-1].assets[0]
        data = self._read_asset_bytes(asset, backend)
        self._verify_provenance(asset, data)
        self._remember_hosted(asset, data)
        return data

    def _remember_hosted(self, asset: Any, data: bytes) -> None:
        """Record the generated asset's hosted URL for later input chaining."""
        url = getattr(asset, "url", None)
        if url and str(url).lower().startswith("https://"):
            self.last_asset_url = str(url)
            self._hosted_by_sha[hashlib.sha256(data).hexdigest()] = str(url)
        else:
            self.last_asset_url = None

    def _fetchable_chained_url(self, hosted: str, backend: Any | None) -> str:
        """Return a chained-input URL the provider's server can actually fetch.

        A generated asset persisted through the storage backend has its ``url``
        rewritten by Genblaze's sink to the backend's DURABLE object URL. For a
        private bucket that URL needs auth — the provider's queue worker fetches
        it unauthenticated, gets a 401, and GMICloud rejects the image-to-video
        submit with a 400 (the illustration-clip failure this fixes). When the
        hosted URL is one of our backend's own object URLs, mint a short-lived
        presigned GET so the worker can read it. A URL the backend doesn't own —
        a genuinely public provider-hosted output (the no-backend chain, or a
        bucket served from a public base) — is already fetchable and passes
        through unchanged.
        """
        if backend is None:
            return hosted
        key = backend.key_from_url(hosted)
        if key is None:
            return hosted
        return backend.get_url(key, expires_in=self._INPUT_URL_EXPIRES_SECS)

    def _external_inputs(
        self, inputs: list[bytes] | None, backend: Any | None
    ) -> list[Any] | None:
        """Turn caller-held input bytes into Genblaze ``external_inputs`` Assets.

        Chained outputs (bytes this instance generated) reuse their hosted
        GMI URL; anything else is persisted through the storage backend under
        a content-addressed key and handed over as a short-lived presigned
        URL. ``sha256`` is sealed on every Asset so step cache keys and the
        Genblaze manifest hash stay stable across reruns.
        """
        if not inputs:
            return None
        from genblaze_core.models.asset import Asset as GbAsset  # type: ignore

        assets: list[Any] = []
        for data in inputs:
            digest = hashlib.sha256(data).hexdigest()
            media_type = _image_media_type(data)
            hosted = self._hosted_by_sha.get(digest)
            if hosted is not None:
                assets.append(GbAsset(url=self._fetchable_chained_url(hosted, backend),
                                      media_type=media_type,
                                      sha256=digest, size_bytes=len(data)))
                continue
            if backend is None:
                raise ValueError(
                    "input media requires either a chained generated asset or "
                    "the Genblaze storage backend so the provider can fetch "
                    "it: configure B2 (B2_BUCKET_NAME + credentials) or "
                    "inject backend=."
                )
            key = f"{self._INPUT_KEY_PREFIX}/{digest}{_MEDIA_TYPE_EXT[media_type]}"
            if not backend.exists(key):
                backend.put(key, data, content_type=media_type)
            url = backend.get_url(key, expires_in=self._INPUT_URL_EXPIRES_SECS)
            assets.append(GbAsset(url=url, media_type=media_type, sha256=digest,
                                  size_bytes=len(data)))
        return assets

    def _read_asset_bytes(self, asset: Any, backend: Any | None) -> bytes:
        """Return the generated asset's bytes.

        When Genblaze persisted to our B2 backend, read the durable object
        back through the *same* backend (no second, unauthenticated network
        fetch); otherwise pull the provider's hosted asset URL via the
        download seam.
        """
        if backend is not None:
            key = backend.key_from_url(asset.url)
            if key is not None:
                return backend.get(key)
        return self._download(asset.url)

    @staticmethod
    def _verify_provenance(asset: Any, data: bytes) -> None:
        """Chain Genblaze's provenance into ClaimScene's.

        If Genblaze sealed a real SHA-256 for this asset, the bytes we return
        MUST match it — otherwise the asset was tampered with in transit.
        (The all-zero placeholder some providers emit pre-sink is ignored.)
        """
        sha = asset.sha256
        if sha and set(sha) != {"0"}:
            got = hashlib.sha256(data).hexdigest()
            if got != sha:
                raise ValueError(
                    f"Genblaze provenance mismatch: sealed sha256={sha}, downloaded={got}"
                )
