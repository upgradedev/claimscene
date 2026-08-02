"""SDK-boundary contract tests for the real Genblaze adapter.

These drive :class:`GenblazeMediaProvider` through a **real**
``genblaze_core.Pipeline`` using the SDK's own shipped mock provider
(``genblaze_core.testing``) — not a ClaimScene fake. Only the terminal
network read (fetching hosted asset bytes) is stubbed; everything else is
the genuine SDK, so any future release that changes these shapes fails CI:

* ``Pipeline(name).step(provider, model=, prompt=, modality=,
  external_inputs=, **params).run(sink=, timeout=, raise_on_failure=True)``
* ``PipelineResult.run`` / ``PipelineResult.manifest`` (``verify_hash()``)
* ``result.run.steps[-1].assets[0]`` → a URL-addressed ``Asset``

The input-attachment tests pin the exact class of live-path bug that bit
our Cinemory entry: a step built WITHOUT ``external_inputs=`` compiles and
runs — and silently drops the input image, so GMI rejects every I2V submit
with ``image (Required parameter is missing)``.

``genblaze-core`` is a pure-Python dependency with no credentials, installed
in CI via ``requirements-dev.txt``, so these tests *run* (they do not skip).
"""
from __future__ import annotations

import hashlib

import pytest

from claimscene.adapters.genblaze_provider import GenblazeMediaProvider

genblaze_core = pytest.importorskip(
    "genblaze_core",
    reason="genblaze-core must be installed for the SDK-boundary contract "
    "tests (it is in requirements-dev.txt; CI installs it)",
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    for name in ("B2_BUCKET_NAME", "B2_S3_ENDPOINT", "B2_ENDPOINT_URL",
                 "B2_APPLICATION_KEY_ID", "B2_KEY_ID", "B2_APPLICATION_KEY",
                 "B2_APP_KEY", "B2_KEY_PREFIX", "B2_PREFIX", "GMI_API_KEY",
                 "GENBLAZE_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


def _mock_provider(assets=None, **kwargs):
    from genblaze_core.testing import MockProvider

    return MockProvider(name="mock-media", assets=assets, **kwargs)


def _gb_asset(url: str, media_type: str, sha256: str, size_bytes: int | None = None):
    from genblaze_core.models.asset import Asset as GbAsset

    return GbAsset(url=url, media_type=media_type, sha256=sha256,
                   size_bytes=size_bytes)


def _mem_backend():
    """A faithful in-memory implementation of Genblaze's ``StorageBackend``
    ABC — exercises the real sink → store → readback path offline.

    ``get_url`` returns a DISTINGUISHABLE presigned form (a ``?sig=`` query the
    durable URL never carries), mirroring a real backend where the presigned
    GET and the persistable durable URL are different strings. Without that
    distinction the chained-input presign regression would be vacuous. The
    prefix is ``https://`` so the adapter's chaining seam (which only remembers
    ``https://`` output URLs) treats it like a real hosted asset.
    """
    from genblaze_core.storage.base import StorageBackend

    class MemBackend(StorageBackend):
        _PREFIX = "https://mem.test/bucket/"

        def __init__(self) -> None:
            self.store: dict[str, bytes] = {}

        def put(self, key, data, *, content_type=None, metadata=None,
                extra_args=None):
            self.store[key] = (bytes(data)
                               if isinstance(data, bytes | bytearray)
                               else data.read())
            return key

        def get(self, key):
            return self.store[key]

        def exists(self, key):
            return key in self.store

        def delete(self, key):
            self.store.pop(key, None)

        def get_url(self, key, *, expires_in=3600):
            # Presigned form — a fetchable GET, distinct from the durable URL.
            return f"{self.get_durable_url(key)}?sig={expires_in}"

        def get_durable_url(self, key):
            # Persistable, credential-free — needs auth to actually GET.
            return f"{self._PREFIX}{key}"

        def key_from_url(self, url):
            if not url.startswith(self._PREFIX):
                return None
            return url[len(self._PREFIX):].split("?", 1)[0]

    return MemBackend()


def _forbid_download(_u: str) -> bytes:
    raise AssertionError("must read back through the backend, not download")


def _adapter_with_stored_clip(clip: bytes):
    """MockProvider + MemBackend wiring where the output clip is already
    durable (the real B2 steady state), so ``generate`` exercises hosting +
    readback with zero network."""
    backend = _mem_backend()
    backend.put("assets/clip.mp4", clip)
    url = backend.get_durable_url("assets/clip.mp4")
    provider = _mock_provider(
        [_gb_asset(url, "video/mp4", hashlib.sha256(clip).hexdigest(),
                   size_bytes=len(clip))])
    adapter = GenblazeMediaProvider(
        video_provider_obj=provider, backend=backend, downloader=_forbid_download)
    return adapter, provider, backend


# ── 1. real pipeline round-trip ──────────────────────────────────────────────
def test_adapter_drives_real_pipeline_and_returns_bytes():
    payload = b"CLAIMSCENE-CLIP" + b"\x00\x01\x02" * 512
    sha = hashlib.sha256(payload).hexdigest()
    url = "https://mock.test/generated.mp4"
    seen: dict = {}

    def fake_downloader(u: str) -> bytes:
        seen["url"] = u
        return payload

    adapter = GenblazeMediaProvider(
        video_provider_obj=_mock_provider([_gb_asset(url, "video/mp4", sha)]),
        downloader=fake_downloader,
    )
    out = adapter.generate(model="pixverse-v6-i2v", prompt="toy diorama orbit")
    assert out == payload
    assert seen["url"] == url
    # Genblaze sealed a manifest for the run and we surfaced it.
    assert adapter.last_manifest is not None
    assert adapter.last_manifest.verify_hash() is True


def test_adapter_reads_back_through_genblaze_sink():
    clip = b"REAL-CLIP-BYTES" + b"\x07\x08" * 400
    adapter, _provider, _backend = _adapter_with_stored_clip(clip)
    out = adapter.generate(model="m", prompt="p")
    assert out == clip  # bytes came back through backend.get(key_from_url(url))
    assert adapter.last_manifest is not None
    assert adapter.last_manifest.verify_hash() is True


def test_image_modality_resolves_the_image_provider():
    still = _PNG_MAGIC + b"still-bytes" * 64
    provider = _mock_provider(
        [_gb_asset("https://mock.test/still.png", "image/png",
                   hashlib.sha256(still).hexdigest())])
    adapter = GenblazeMediaProvider(image_provider_obj=provider,
                                    downloader=lambda _u: still)
    out = adapter.generate(model="seedream-5.0-lite", prompt="toy diorama",
                           modality="image",
                           params={"size": "2K", "output_format": "png"})
    assert out == still
    assert provider.received_steps[-1].model == "seedream-5.0-lite"


def test_unsupported_modality_is_rejected():
    adapter = GenblazeMediaProvider(video_provider_obj=_mock_provider())
    with pytest.raises(ValueError, match="modality"):
        adapter.generate(model="m", prompt="p", modality="audio")


# ── 2. provenance chaining ───────────────────────────────────────────────────
def test_provenance_mismatch_is_rejected():
    adapter = GenblazeMediaProvider(
        video_provider_obj=_mock_provider(
            [_gb_asset("https://mock.test/tampered.mp4", "video/mp4", "a" * 64)]),
        downloader=lambda _u: b"different-bytes-than-were-sealed",
    )
    with pytest.raises(ValueError, match="provenance mismatch"):
        adapter.generate(model="mock", prompt="x")


def test_generation_failure_raises():
    adapter = GenblazeMediaProvider(
        video_provider_obj=_mock_provider(should_fail=True),
        downloader=lambda _u: b"never-reached",
    )
    with pytest.raises(Exception):  # noqa: B017 - SDK raises PipelineError/ProviderError
        adapter.generate(model="mock", prompt="x")


# ── 3. SDK signature drift guards ────────────────────────────────────────────
def test_sdk_signatures_match_adapter_assumptions():
    import inspect

    from genblaze_core import Modality as GbModality
    from genblaze_core import ObjectStorageSink, Pipeline, PipelineResult

    step_params = inspect.signature(Pipeline.step).parameters
    for p in ("provider", "model", "prompt", "modality", "external_inputs"):
        assert p in step_params, f"Pipeline.step lost parameter {p!r}"

    run_params = inspect.signature(Pipeline.run).parameters
    for p in ("sink", "timeout", "raise_on_failure"):
        assert p in run_params, f"Pipeline.run lost parameter {p!r}"

    result_params = inspect.signature(PipelineResult.__init__).parameters
    assert "run" in result_params and "manifest" in result_params

    sink_params = list(inspect.signature(ObjectStorageSink.__init__).parameters)
    assert sink_params[1] == "backend"

    for name in ("IMAGE", "VIDEO"):  # the two modalities the adapter maps
        assert hasattr(GbModality, name)


def test_gmicloud_provider_classes_importable_when_installed():
    gmi = pytest.importorskip(
        "genblaze_gmicloud",
        reason="optional live extra 'genblaze[gmicloud]' not installed",
    )
    for cls in ("GMICloudVideoProvider", "GMICloudImageProvider"):
        assert hasattr(gmi, cls), f"genblaze_gmicloud missing {cls}"


def test_pixverse_v6_i2v_routes_image_to_native_slot():
    """Pin the live routing the illustration clip depends on.

    gmicloud's real video ``ModelRegistry`` must resolve ``pixverse-v6-i2v`` to
    the pixverse family and route the chained input image to the native
    ``image`` slot with ``quality`` + ``duration`` on the allowlist. If a
    future SDK bump drops the pixverse family, the slug falls to the permissive
    fallback and the payload shape drifts — which is exactly what 400s the
    live GMICloud submit. RUNS in CI: genblaze-gmicloud is in
    requirements-dev.txt (pure-Python, no key, no network at prepare_payload).
    """
    video_models = pytest.importorskip(
        "genblaze_gmicloud.models.video",
        reason="genblaze-gmicloud (requirements-dev.txt) ships the video registry",
    )
    from genblaze_core.models.asset import Asset as GbAsset
    from genblaze_core.models.enums import Modality
    from genblaze_core.models.step import Step

    reg = video_models.build_video_registry()
    match = reg.match_family("pixverse-v6-i2v")
    assert match is not None, "pixverse-v6-i2v no longer matches a family (would hit fallback)"
    assert match.family.name == "gmi-video-pixverse"

    spec = reg.get("pixverse-v6-i2v")
    for allowed in ("image", "quality", "duration", "aspect_ratio"):
        assert allowed in spec.param_allowlist, f"pixverse spec dropped {allowed!r}"

    img = GbAsset(url="https://host.test/chain-inputs/still.png",
                  media_type="image/png", sha256="a" * 64, size_bytes=10)
    # "720p" / "4:3" -- kept in sync with pipeline.py's own
    # ILLUSTRATION_CLIP_QUALITY / ILLUSTRATION_CLIP_ASPECT_RATIO constants
    # (see their docstring for how "720p" was determined) so nothing here
    # still implies the old "360p" is what ClaimScene actually sends; the
    # SDK plumbing this test pins is agnostic to the literal value either
    # way.
    step = Step(provider="gmicloud", model="pixverse-v6-i2v", prompt="orbit",
                modality=Modality.VIDEO,
                params={"duration": 5, "quality": "720p", "aspect_ratio": "4:3"},
                inputs=[img])
    payload = reg.prepare_payload(step)
    # The image URL lands in the native ``image`` slot (not dropped, not misrouted).
    assert payload["image"] == "https://host.test/chain-inputs/still.png"
    assert payload["duration"] == 5
    assert payload["quality"] == "720p"
    assert payload["aspect_ratio"] == "4:3"
    assert payload["prompt"] == "orbit"


# ── 4. input attachment (the Cinemory PR #15 lesson) ─────────────────────────
def test_generate_attaches_image_input_as_external_asset():
    photo = _PNG_MAGIC + b"real-photo-bytes" * 32
    photo_sha = hashlib.sha256(photo).hexdigest()
    clip = b"CLIP" + b"\x03\x04" * 400
    adapter, provider, backend = _adapter_with_stored_clip(clip)

    out = adapter.generate(model="pixverse-v6-i2v", prompt="orbit",
                           inputs=[photo])

    assert out == clip
    # The Step the SDK handed the provider carries the image as an input Asset.
    step = provider.received_steps[-1]
    assert len(step.inputs) == 1, "input was dropped before reaching the provider"
    asset = step.inputs[0]
    assert asset.sha256 == photo_sha
    assert asset.media_type == "image/png"
    assert asset.size_bytes == len(photo)
    # Hosted through the SAME backend the sink uses, content-addressed.
    key = backend.key_from_url(asset.url)
    assert key == f"chain-inputs/{photo_sha}.png"
    assert backend.get(key) == photo


def test_pipeline_step_receives_external_inputs_kwarg(monkeypatch):
    """Pin the exact regression: the step must be built WITH
    ``external_inputs=`` (a step built without it compiles and runs — and
    silently drops the image)."""
    from genblaze_core import Pipeline

    seen: dict = {}
    real_step = Pipeline.step

    def spying_step(self, provider, **kwargs):
        seen.update(kwargs)
        return real_step(self, provider, **kwargs)

    monkeypatch.setattr(Pipeline, "step", spying_step)

    photo = _PNG_MAGIC + b"spy-photo" * 16
    clip = b"SPYCLIP" + b"\x07" * 200
    adapter, _provider, _backend = _adapter_with_stored_clip(clip)
    adapter.generate(model="m", prompt="p", inputs=[photo])

    external = seen.get("external_inputs")
    assert external, "Pipeline.step was not given external_inputs="
    assert len(external) == 1
    assert external[0].sha256 == hashlib.sha256(photo).hexdigest()


def test_generate_without_inputs_passes_no_external_assets():
    clip = b"T2V" + b"\x08" * 200
    adapter, provider, backend = _adapter_with_stored_clip(clip)
    adapter.generate(model="pixverse-v6-i2v", prompt="p")
    assert provider.received_steps[-1].inputs == []
    assert not [k for k in backend.store if k.startswith("chain-inputs/")]


def test_explicit_none_backend_disables_the_sink_even_with_b2_env(monkeypatch):
    """``backend=None`` must mean NO backend/sink — even when B2 env vars are
    present. Pins the live incident where env-resolved B2 creds without
    PutObject entitlement made every generation die in the sink transfer."""
    monkeypatch.setenv("B2_BUCKET_NAME", "some-bucket")
    payload = b"NOSINK" + b"\x01\x02" * 128
    sha = hashlib.sha256(payload).hexdigest()
    adapter = GenblazeMediaProvider(
        video_provider_obj=_mock_provider(
            [_gb_asset("https://mock.test/c.mp4", "video/mp4", sha)]),
        backend=None,
        downloader=lambda _u: payload,
    )
    assert adapter.generate(model="m", prompt="p") == payload


def test_inputs_without_backend_are_rejected_loudly():
    """No storage backend and no chained asset means the provider could never
    fetch the image — fail fast with an actionable message instead of an
    opaque upstream 400."""
    adapter = GenblazeMediaProvider(
        video_provider_obj=_mock_provider(
            [_gb_asset("https://mock.test/clip.mp4", "video/mp4", "0" * 64)]),
        downloader=lambda _u: b"never-reached",
    )
    with pytest.raises(ValueError, match="storage backend"):
        adapter.generate(model="m", prompt="p", inputs=[_PNG_MAGIC + b"photo"])


# ── 5. still → clip chaining (ClaimScene's establish-shot pattern) ───────────
def test_generated_still_chains_into_the_clip_without_self_hosting():
    """The establish-shot still this adapter just generated must feed the
    i2v clip via its GMI-hosted output URL — no storage backend required
    (probe-verified: GMI output URLs chain directly)."""
    still = _PNG_MAGIC + b"still-frame" * 40
    still_sha = hashlib.sha256(still).hexdigest()
    still_url = "https://mock.test/hosted-still.png"
    clip = b"CHAINED-CLIP" + b"\x05" * 300
    clip_sha = hashlib.sha256(clip).hexdigest()

    downloads = {"https://mock.test/hosted-still.png": still,
                 "https://mock.test/clip.mp4": clip}
    image_provider = _mock_provider([_gb_asset(still_url, "image/png", still_sha)])
    video_provider = _mock_provider(
        [_gb_asset("https://mock.test/clip.mp4", "video/mp4", clip_sha)])
    adapter = GenblazeMediaProvider(
        image_provider_obj=image_provider,
        video_provider_obj=video_provider,
        downloader=lambda u: downloads[u],
    )

    got_still = adapter.generate(model="seedream-5.0-lite", prompt="diorama",
                                 modality="image")
    assert got_still == still
    assert adapter.last_asset_url == still_url

    got_clip = adapter.generate(model="pixverse-v6-i2v", prompt="orbit",
                                modality="video", inputs=[still])
    assert got_clip == clip
    step = video_provider.received_steps[-1]
    assert len(step.inputs) == 1
    assert step.inputs[0].url == still_url  # the hosted URL was reused
    assert step.inputs[0].sha256 == still_sha


def test_chained_still_input_is_presigned_not_raw_durable():
    """The live illustration-clip fix (H2 regression guard).

    With a storage backend attached, Genblaze's sink rewrites the establish
    still's output URL to the backend's DURABLE (private) object URL. Chaining
    that raw URL into the i2v clip makes the provider's queue worker fetch it
    unauthenticated → 401 → GMICloud rejects the submit with a 400 (the exact
    live failure). The chained input MUST instead be re-presigned to a
    fetchable GET for the *same* object.
    """
    backend = _mem_backend()
    still = _PNG_MAGIC + b"establish-shot" * 40
    still_sha = hashlib.sha256(still).hexdigest()
    clip = b"CHAINED-CLIP" + b"\x05" * 300
    clip_sha = hashlib.sha256(clip).hexdigest()
    # The sink already persisted both and rewrote their URLs to the durable form.
    backend.put("assets/still.png", still)
    backend.put("assets/clip.mp4", clip)
    still_durable = backend.get_durable_url("assets/still.png")
    clip_durable = backend.get_durable_url("assets/clip.mp4")

    image_provider = _mock_provider(
        [_gb_asset(still_durable, "image/png", still_sha, size_bytes=len(still))])
    video_provider = _mock_provider(
        [_gb_asset(clip_durable, "video/mp4", clip_sha, size_bytes=len(clip))])
    adapter = GenblazeMediaProvider(
        image_provider_obj=image_provider, video_provider_obj=video_provider,
        backend=backend, downloader=_forbid_download)

    got_still = adapter.generate(model="seedream-5.0-lite", prompt="diorama",
                                 modality="image")
    assert got_still == still
    # The adapter remembered the private durable URL — what the sink leaves behind.
    assert adapter.last_asset_url == still_durable

    adapter.generate(model="pixverse-v6-i2v", prompt="orbit", modality="video",
                     inputs=[still])
    chained = video_provider.received_steps[-1].inputs[0].url
    assert chained != still_durable, "raw private durable URL would 401 the provider fetch"
    # Same object, presigned into a fetchable GET.
    assert chained == backend.get_url(backend.key_from_url(still_durable), expires_in=3600)
    assert "?sig=" in chained  # the distinguishable presigned form
    assert backend.key_from_url(chained) == "assets/still.png"
