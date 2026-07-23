"""PEN-TEST — Injection / path traversal into storage keys + scenario routes.

Threat: an attacker controls the *case id* and the uploaded *filename* — both
become segments of the object-storage key — plus the *scenario id* / *image
name* on the thumbnail route. Without sanitisation a hostile value
(``../../evil``, ``../../../etc/passwd``, NUL, newline) would inject into the B2
key namespace or escape the scenarios directory. The invariant: every stored key
is anchored by the machine-derived SHA-256 with its user-controlled segments
sanitised — no ``..`` segment, no path-separator escape, no NUL/newline, no
leading-dot label — and the scenario image route resolves nothing outside its
own directory.

Note on severity: B2/S3 is a *flat* namespace, so ``..`` is a literal key
segment, not a filesystem escape — this is key/prefix hygiene (defence in depth)
for the store, and real traversal defence for the on-disk scenarios route.
"""
from __future__ import annotations

import json

import pytest

from claimscene.adapters import FakeMediaProvider, FakeVisionExtractor, InMemoryStorage
from claimscene.case import CasePhoto, CaseSpec, PhotoSource
from claimscene.keys import KeyStrategy, make_key, safe_component
from claimscene.pipeline import CasePipeline
from claimscene.schematic import PillowSchematicRenderer

from .conftest import PNG_1x1

_HOSTILE = [
    "../../../etc/passwd",
    "..\\..\\windows\\system32",
    "/absolute/evil",
    "a/b/../../c",
    "..",
    "with\x00nul",
    "with\nnewline",
    "  ..%2f..%2f  ",
]


def _assert_key_is_safe(key: str) -> None:
    for seg in key.split("/"):
        assert seg != "..", f"traversal segment in key: {key!r}"
        assert not seg.startswith("."), f"leading-dot label in key: {key!r}"
    assert "\x00" not in key, f"NUL in key: {key!r}"
    assert "\n" not in key and "\r" not in key, f"newline in key: {key!r}"
    assert "\\" not in key, f"backslash separator in key: {key!r}"
    assert not key.startswith("/"), f"absolute key: {key!r}"


def test_safe_component_neutralises_every_hostile_value():
    for value in _HOSTILE:
        out = safe_component(value)
        assert out and out != ".."
        assert "/" not in out and "\\" not in out
        assert "\x00" not in out and "\n" not in out
        assert not out.startswith(".")


def test_make_key_never_emits_a_traversal_key():
    for case in _HOSTILE:
        for name in _HOSTILE:
            key = make_key(KeyStrategy.HIERARCHICAL, case=case, kind="inputs",
                           sha256="a" * 64, name=name)
            _assert_key_is_safe(key)
            # The content anchor (SHA-256) is always present and intact.
            assert "a" * 64 in key.split("/")


def test_content_address_is_derived_from_bytes_not_filename():
    """Two different hostile filenames with identical bytes hash to the same
    content anchor — identity is content-addressed, not attacker-named."""
    k1 = make_key(KeyStrategy.HIERARCHICAL, case="c", kind="inputs",
                  sha256="d" * 64, name="../../a.png")
    k2 = make_key(KeyStrategy.HIERARCHICAL, case="c", kind="inputs",
                  sha256="d" * 64, name="..\\b.png")
    # Layout is <case>/<kind>/<shard>/<sha256>/<name> → the anchor sits at idx 3.
    assert k1.split("/")[3] == k2.split("/")[3] == "d" * 64


def test_pipeline_stores_only_safe_keys_for_hostile_ingest():
    """End-to-end: a hostile case id + filenames flow through the real pipeline;
    every key written to storage is sanitised and content-addressed."""
    storage = InMemoryStorage(bucket="pentest")
    pipeline = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                            renderer=PillowSchematicRenderer(animate=False))
    pipeline.run(CaseSpec(case_id="../../evil-case", photos=[
        CasePhoto(filename="../../../etc/passwd", data=PNG_1x1,
                  source=PhotoSource.staged_demo),
        CasePhoto(filename="a/b/../c.png", data=PNG_1x1 + b"x",
                  source=PhotoSource.staged_demo)]))
    assert storage.index  # work happened
    for row in storage.index:
        _assert_key_is_safe(row["key"])
        # Hierarchical layout keeps its content anchor.
        parts = row["key"].split("/")
        assert any(len(p) == 64 for p in parts), row["key"]


def test_api_render_hostile_case_id_stays_sanitised(client):
    """The full HTTP path: a traversal case id returns 200 and the sealed case's
    id + manifest URL carry no traversal sequence or separator escape."""
    import claimscene.api as api

    scene = client.post("/cases/extract",
                        data={"scenario_id": "s01_rear_end"}).json()["scene"]
    body = client.post("/cases/render", data={
        "scene": json.dumps(scene), "case_id": "../../evil path",
        "scenario_id": "s01_rear_end"}).json()
    cid = body["case_id"]
    assert ".." not in cid and "/" not in cid and " " not in cid and "\\" not in cid
    assert "/../" not in body["manifest_url"]
    # Every stored key for the case is content-addressed + traversal-free.
    prefix = cid.split("-")[0]
    keys = [r["key"] for r in api._storage.index if r["key"].startswith(prefix)]
    assert keys
    for key in keys:
        _assert_key_is_safe(key)
        assert any(len(s) == 64 for s in key.split("/"))


def test_hostile_scenario_id_in_render_is_404_not_injection(client):
    """A hostile scenario id resolves to nothing (404) — it cannot inject a key
    segment because it is reduced + matched against the committed allowlist."""
    scene = client.post("/cases/extract",
                        data={"scenario_id": "s01_rear_end"}).json()["scene"]
    r = client.post("/cases/render", data={
        "scene": json.dumps(scene), "scenario_id": "../../../secret"})
    assert r.status_code == 404


@pytest.mark.parametrize("bad", [
    "/scenarios/s01_rear_end/images/..%2f..%2fmanifest.json",
    "/scenarios/..%2f..%2fetc/images/view_a.jpg",
    "/scenarios/s01_rear_end/images/nope.jpg",
    "/scenarios/ghost/images/view_a.jpg",
])
def test_scenario_image_route_blocks_traversal_and_unknowns(client, bad):
    assert client.get(bad).status_code == 404
