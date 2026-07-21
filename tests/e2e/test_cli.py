"""CLI smoke: the judge-runnable proof produces artifacts + verify PASS."""
from __future__ import annotations

import json

import pytest
from tests.conftest import PNG_1x1

from claimscene.cli import main
from claimscene.provenance import verify_manifest

EXPECTED_FILES = {"scene.json", "timeline.json", "schematic.svg", "schematic.png",
                  "illustration.mp4", "report.md", "manifest.json", "index.jsonl"}


def test_cli_with_photo_directory(tmp_path, capsys):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "scene_front.png").write_bytes(PNG_1x1 + b"1")
    (photos / "damage_side.png").write_bytes(PNG_1x1 + b"2")
    (photos / "road_wide.png").write_bytes(PNG_1x1 + b"3")
    (photos / "notes.txt").write_text("ignored")
    out = tmp_path / "out"

    rc = main(["--case", "cli-case", "--photos", str(photos), "--out", str(out)])
    captured = capsys.readouterr().out
    assert rc == 0
    assert "VERIFY: PASS" in captured
    produced = {p.name for p in (out / "cli-case").iterdir()}
    assert EXPECTED_FILES <= produced

    manifest = json.loads((out / "cli-case" / "manifest.json").read_text("utf-8"))
    assert verify_manifest(manifest)
    roles = [row["role"] for row in manifest["inputs"]]
    assert roles == ["damage_photo", "road_photo", "scene_photo"]  # sorted filenames
    assert all(row["source"] == "staged_demo" for row in manifest["inputs"])


def test_cli_without_photos_generates_synthetic_inputs(tmp_path, capsys):
    out = tmp_path / "out"
    rc = main(["--case", "synth", "--out", str(out)])
    captured = capsys.readouterr().out
    assert rc == 0
    assert "generating synthetic demo inputs" in captured
    manifest = json.loads((out / "synth" / "manifest.json").read_text("utf-8"))
    assert all(row["source"] == "synthetic_generated" for row in manifest["inputs"])
    assert manifest["illustration"]["degraded"] is True


def test_cli_source_flag_recorded(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "p.png").write_bytes(PNG_1x1)
    out = tmp_path / "out"
    rc = main(["--case", "lic", "--photos", str(photos), "--out", str(out),
               "--source", "licensed"])
    assert rc == 0
    manifest = json.loads((out / "lic" / "manifest.json").read_text("utf-8"))
    assert manifest["inputs"][0]["source"] == "licensed"


def test_cli_rejects_unknown_source(tmp_path):
    with pytest.raises(SystemExit):
        main(["--case", "x", "--out", str(tmp_path), "--source", "stolen"])
