"""Adapter selection + Backblaze B2 credential resolution.

``CLAIMSCENE_MODE=offline`` (default) wires the fakes so the app runs with
no credentials — used by CI and local demos. ``CLAIMSCENE_MODE=live`` wires
the real adapters *when they are ready* (credentials + SDK present) and
degrades transparently to the fakes otherwise, logging a WARNING — a
credential-free run must still produce a full sealed case, never crash.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass

from .adapters import FakeMediaProvider, FakeVisionExtractor, InMemoryStorage
from .ports import MediaProvider, StorageBackend, VisionExtractor

_log = logging.getLogger("claimscene.config")


def mode() -> str:
    return os.environ.get("CLAIMSCENE_MODE", "offline").lower()


# ── Backblaze B2 credential resolution ───────────────────────────────────────
# Canonical Backblaze names are primary; legacy aliases are also accepted:
#
#   field     canonical (primary)      legacy alias
#   -------   ----------------------   ---------------
#   key_id    B2_APPLICATION_KEY_ID    B2_KEY_ID
#   app_key   B2_APPLICATION_KEY       B2_APP_KEY
#   bucket    B2_BUCKET_NAME           (same)
#   endpoint  B2_S3_ENDPOINT           B2_ENDPOINT_URL
#
# Resolution is a pure function with no side effects; the offline default
# never touches it, so credential-free CI and local demos are unaffected.


@dataclass(frozen=True)
class B2Config:
    """Resolved Backblaze B2 settings (any field may be ``None`` if unset)."""

    bucket: str | None
    endpoint_url: str | None
    key_id: str | None
    app_key: str | None
    key_prefix: str | None


def _first_env(*names: str) -> str | None:
    """First non-empty environment value among ``names`` (precedence = order)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _normalize_endpoint(url: str | None) -> str | None:
    """Ensure an endpoint has an explicit scheme (default ``https://``)."""
    if not url:
        return url
    if "://" in url:
        return url
    return f"https://{url}"


def resolve_b2_config() -> B2Config:
    """Resolve B2 settings from the environment (canonical name, then alias)."""
    return B2Config(
        bucket=_first_env("B2_BUCKET_NAME"),
        endpoint_url=_normalize_endpoint(_first_env("B2_S3_ENDPOINT", "B2_ENDPOINT_URL")),
        key_id=_first_env("B2_APPLICATION_KEY_ID", "B2_KEY_ID"),
        app_key=_first_env("B2_APPLICATION_KEY", "B2_APP_KEY"),
        key_prefix=_first_env("B2_KEY_PREFIX", "B2_PREFIX"),
    )


# ── live-capability detection (degrade-to-offline is the safe default) ────────
def _b2_ready() -> bool:
    """True when the real B2 storage adapter can actually be constructed."""
    if importlib.util.find_spec("boto3") is None:
        return False
    cfg = resolve_b2_config()
    return all((cfg.bucket, cfg.endpoint_url, cfg.key_id, cfg.app_key))


def storage_ready() -> bool:
    return mode() == "live" and _b2_ready()


def build_storage() -> StorageBackend:
    if storage_ready():
        from .adapters.b2_storage import B2Storage

        return B2Storage()
    if mode() == "live":
        _log.warning(
            "CLAIMSCENE_MODE=live but B2 storage is not ready (missing B2 "
            "credentials or boto3); using the offline object store so case "
            "processing still works."
        )
    return InMemoryStorage()


def _vlm_keys_present() -> bool:
    if os.environ.get("GMI_API_KEY"):
        return True
    return bool(os.environ.get("NEBIUS_INFERENCE_API_KEY")
                and os.environ.get("NEBIUS_INFERENCE_BASE_URL"))


def vlm_ready() -> bool:
    """True when the live VLM ladder can be constructed (key + openai pkg)."""
    return (mode() == "live" and _vlm_keys_present()
            and importlib.util.find_spec("openai") is not None)


def build_extractor() -> VisionExtractor:
    if vlm_ready():
        from .adapters.vlm_extractor import VlmExtractor

        return VlmExtractor()
    if mode() == "live":
        _log.warning(
            "CLAIMSCENE_MODE=live but the VLM ladder is not ready (need "
            "GMI_API_KEY or NEBIUS_INFERENCE_* plus the openai package — "
            "pip install claimscene[live]); using the offline deterministic "
            "extractor."
        )
    return FakeVisionExtractor()


def provider_ready() -> bool:
    """True when the live Genblaze provider can be constructed (key + SDK)."""
    return (mode() == "live" and bool(os.environ.get("GMI_API_KEY"))
            and importlib.util.find_spec("genblaze_gmicloud") is not None)


def build_provider() -> MediaProvider:
    if provider_ready():
        from .adapters.genblaze_provider import GenblazeMediaProvider

        return GenblazeMediaProvider()
    if mode() == "live":
        _log.warning(
            "CLAIMSCENE_MODE=live but the Genblaze provider is not ready "
            "(need GMI_API_KEY plus the genblaze SDK — pip install "
            "claimscene[live]); using the offline media provider."
        )
    return FakeMediaProvider()
