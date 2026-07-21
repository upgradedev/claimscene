"""Mode + credential resolution; live-without-creds must degrade, not crash."""
from __future__ import annotations

from claimscene import config
from claimscene.adapters.fakes import (
    FakeMediaProvider,
    FakeVisionExtractor,
    InMemoryStorage,
)


def test_default_mode_is_offline(clean_b2_env):
    assert config.mode() == "offline"


def test_resolve_canonical_names(clean_b2_env, monkeypatch):
    monkeypatch.setenv("B2_BUCKET_NAME", "bucket-x")
    monkeypatch.setenv("B2_S3_ENDPOINT", "s3.eu-central-003.backblazeb2.com")
    monkeypatch.setenv("B2_APPLICATION_KEY_ID", "kid")
    monkeypatch.setenv("B2_APPLICATION_KEY", "key")
    cfg = config.resolve_b2_config()
    assert cfg.bucket == "bucket-x"
    assert cfg.endpoint_url == "https://s3.eu-central-003.backblazeb2.com"  # scheme added
    assert cfg.key_id == "kid" and cfg.app_key == "key"


def test_resolve_legacy_aliases_and_precedence(clean_b2_env, monkeypatch):
    monkeypatch.setenv("B2_ENDPOINT_URL", "https://legacy.example")
    monkeypatch.setenv("B2_KEY_ID", "legacy-kid")
    monkeypatch.setenv("B2_APP_KEY", "legacy-key")
    cfg = config.resolve_b2_config()
    assert cfg.endpoint_url == "https://legacy.example"
    assert cfg.key_id == "legacy-kid" and cfg.app_key == "legacy-key"
    # Canonical names win when both are set.
    monkeypatch.setenv("B2_APPLICATION_KEY_ID", "canonical-kid")
    assert config.resolve_b2_config().key_id == "canonical-kid"


def test_live_without_creds_degrades_to_fakes(clean_b2_env, monkeypatch, caplog):
    monkeypatch.setenv("CLAIMSCENE_MODE", "live")
    with caplog.at_level("WARNING", logger="claimscene.config"):
        storage = config.build_storage()
        provider = config.build_provider()
        extractor = config.build_extractor()
    assert isinstance(storage, InMemoryStorage)
    assert isinstance(provider, FakeMediaProvider)
    assert isinstance(extractor, FakeVisionExtractor)
    assert sum("using the offline" in r.message for r in caplog.records) >= 3


def test_offline_builds_fakes_silently(clean_b2_env, caplog):
    with caplog.at_level("WARNING", logger="claimscene.config"):
        storage = config.build_storage()
    assert isinstance(storage, InMemoryStorage)
    assert not caplog.records


def test_live_with_keys_and_sdks_builds_live_adapters(clean_b2_env, monkeypatch):
    import importlib.util as ilu

    monkeypatch.setenv("CLAIMSCENE_MODE", "live")
    monkeypatch.setenv("GMI_API_KEY", "test-key")
    real_find_spec = ilu.find_spec
    monkeypatch.setattr(
        ilu, "find_spec",
        lambda name, *a: object() if name in ("openai", "genblaze_gmicloud")
        else real_find_spec(name, *a))

    from claimscene.adapters.genblaze_provider import GenblazeMediaProvider
    from claimscene.adapters.vlm_extractor import VlmExtractor

    assert config.vlm_ready() and config.provider_ready()
    extractor = config.build_extractor()
    provider = config.build_provider()
    assert isinstance(extractor, VlmExtractor)
    assert [r.model for r in extractor.rungs] == [
        "google/gemma-4-31b-it", "google/gemini-3.5-flash"]
    assert isinstance(provider, GenblazeMediaProvider)


def test_live_key_without_sdk_degrades_with_actionable_warning(
        clean_b2_env, monkeypatch, caplog):
    import importlib.util as ilu

    monkeypatch.setenv("CLAIMSCENE_MODE", "live")
    monkeypatch.setenv("GMI_API_KEY", "test-key")
    real_find_spec = ilu.find_spec
    monkeypatch.setattr(
        ilu, "find_spec",
        lambda name, *a: None if name in ("openai", "genblaze_gmicloud")
        else real_find_spec(name, *a))
    with caplog.at_level("WARNING", logger="claimscene.config"):
        extractor = config.build_extractor()
        provider = config.build_provider()
    assert isinstance(extractor, FakeVisionExtractor)
    assert isinstance(provider, FakeMediaProvider)
    assert sum("claimscene[live]" in r.message for r in caplog.records) == 2
