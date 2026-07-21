"""VlmExtractor: canned-transport tests — ladder, repair, parsing. No network."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from tests.conftest import PNG_1x1

from claimscene.adapters.vlm_extractor import (
    VlmExtractionError,
    VlmExtractor,
    VlmRung,
    build_system_prompt,
    default_ladder,
    extract_json_object,
    normalize_scene_payload,
)
from claimscene.case import CasePhoto, PhotoSource
from claimscene.scene import SceneGraph

VALID_SCENE = {
    "schema": "claimscene/scene/v1",
    "road": {"layout": "straight", "lanes_per_direction": 1,
             "signal": "traffic_light"},
    "vehicles": [
        {"id": "veh_a", "kind": "car", "color": "blue",
         "damage": [{"clock_position": 6, "severity": "crush"}]},
        {"id": "veh_b", "kind": "car", "color": "red",
         "damage": [{"clock_position": 12, "severity": "dent"}]},
    ],
    "movements": [
        {"vehicle_id": "veh_a", "approach": "N", "maneuver": "straight",
         "speed_band": "stopped"},
        {"vehicle_id": "veh_b", "approach": "N", "maneuver": "straight",
         "speed_band": "moderate"},
    ],
    "impacts": [{"vehicle_id": "veh_a", "clock_position": 6},
                {"vehicle_id": "veh_b", "clock_position": 12}],
    "sequence": ["vehicles_approach", "impact", "vehicles_come_to_rest"],
}


class _ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


def _reply(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class _StubClient:
    """OpenAI-shaped stub: pops one outcome (text or exception) per call."""

    def __init__(self, outcomes: list) -> None:
        self.calls: list[dict] = []
        self._outcomes = outcomes
        parent = self

        class _Completions:
            def create(self, **kwargs):
                parent.calls.append(kwargs)
                outcome = parent._outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return _reply(outcome)

        self.chat = SimpleNamespace(completions=_Completions())


def _rung(name: str, base: str) -> VlmRung:
    return VlmRung(name=name, base_url=base, api_key="k", model=name)


def _extractor(stubs: dict[str, _StubClient], rungs: list[VlmRung],
               **kwargs) -> VlmExtractor:
    sleeps: list[float] = []
    ex = VlmExtractor(
        rungs,
        client_factory=lambda base_url, api_key: stubs[base_url],
        sleeper=sleeps.append,
        **kwargs,
    )
    ex._test_sleeps = sleeps  # type: ignore[attr-defined]
    return ex


@pytest.fixture
def photos() -> list[CasePhoto]:
    return [CasePhoto(filename="scene.png", data=PNG_1x1,
                      source=PhotoSource.staged_demo)]


# ── happy path ───────────────────────────────────────────────────────────────
def test_valid_reply_becomes_validated_scene(photos):
    stub = _StubClient([json.dumps(VALID_SCENE)])
    ex = _extractor({"https://a.test/v1": stub},
                    [_rung("rung-a", "https://a.test/v1")])
    scene = ex.extract(photos, context="rear-end on the avenue")
    assert isinstance(scene, SceneGraph)
    assert [v.id for v in scene.vehicles] == ["veh_a", "veh_b"]
    assert ex.last_rung == "rung-a"
    assert any("rung-a" in note for note in scene.confidence_notes)
    # The transport got the constrained-vocabulary system prompt + the photos.
    call = stub.calls[0]
    assert call["model"] == "rung-a"
    assert call["temperature"] == 0.0
    assert call["messages"][0]["role"] == "system"
    image_blocks = [b for b in call["messages"][1]["content"]
                    if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/")
    texts = [b["text"] for b in call["messages"][1]["content"]
             if b.get("type") == "text"]
    assert any("rear-end on the avenue" in t for t in texts)


def test_fenced_reply_with_null_fields_is_tolerated(photos):
    scene = json.loads(json.dumps(VALID_SCENE))
    scene["vehicles"][0]["damage"][0]["note"] = None  # present-but-null
    scene["confidence_notes"] = None
    stub = _StubClient(["```json\n" + json.dumps(scene) + "\n```"])
    ex = _extractor({"https://a.test/v1": stub},
                    [_rung("rung-a", "https://a.test/v1")])
    out = ex.extract(photos)
    assert out.vehicles[0].damage[0].note is None


# ── repair round-trip ────────────────────────────────────────────────────────
def test_invalid_reply_gets_one_repair_roundtrip(photos):
    bad = json.loads(json.dumps(VALID_SCENE))
    bad["gps"] = [38.0, 23.7]  # hallucinated field → extra="forbid" rejects
    stub = _StubClient([json.dumps(bad), json.dumps(VALID_SCENE)])
    ex = _extractor({"https://a.test/v1": stub},
                    [_rung("rung-a", "https://a.test/v1")])
    out = ex.extract(photos)
    assert isinstance(out, SceneGraph)
    assert len(stub.calls) == 2
    repair_messages = stub.calls[1]["messages"]
    assert repair_messages[2]["role"] == "assistant"  # its own reply fed back
    assert "rejected" in repair_messages[3]["content"]
    assert "gps" in repair_messages[3]["content"]  # the validator's complaint


def test_failed_repair_falls_through_to_next_rung(photos):
    ex = _extractor(
        {"https://a.test/v1": _StubClient(["not json", "still not json"]),
         "https://b.test/v1": _StubClient([json.dumps(VALID_SCENE)])},
        [_rung("rung-a", "https://a.test/v1"),
         _rung("rung-b", "https://b.test/v1")],
    )
    out = ex.extract(photos)
    assert isinstance(out, SceneGraph)
    assert ex.last_rung == "rung-b"


# ── ladder + retry ───────────────────────────────────────────────────────────
def test_retryable_429_is_retried_with_backoff_then_falls_over(photos):
    stub_a = _StubClient([_ApiError(429), _ApiError(429)])
    stub_b = _StubClient([json.dumps(VALID_SCENE)])
    ex = _extractor(
        {"https://a.test/v1": stub_a, "https://b.test/v1": stub_b},
        [_rung("rung-a", "https://a.test/v1"),
         _rung("rung-b", "https://b.test/v1")],
    )
    out = ex.extract(photos)
    assert isinstance(out, SceneGraph)
    assert len(stub_a.calls) == 2  # one in-rung retry
    assert ex._test_sleeps == [2.0]  # backoff between the two attempts
    assert ex.last_rung == "rung-b"


def test_non_retryable_error_moves_to_next_rung_immediately(photos):
    stub_a = _StubClient([_ApiError(400)])
    stub_b = _StubClient([json.dumps(VALID_SCENE)])
    ex = _extractor(
        {"https://a.test/v1": stub_a, "https://b.test/v1": stub_b},
        [_rung("rung-a", "https://a.test/v1"),
         _rung("rung-b", "https://b.test/v1")],
    )
    ex.extract(photos)
    assert len(stub_a.calls) == 1  # no retry burned on a 400
    assert ex._test_sleeps == []


def test_all_rungs_exhausted_raises_with_reasons(photos):
    ex = _extractor(
        {"https://a.test/v1": _StubClient([_ApiError(503), _ApiError(503)]),
         "https://b.test/v1": _StubClient(["gibberish", "more gibberish"])},
        [_rung("rung-a", "https://a.test/v1"),
         _rung("rung-b", "https://b.test/v1")],
    )
    with pytest.raises(VlmExtractionError) as exc:
        ex.extract(photos)
    message = str(exc.value)
    assert "rung-a" in message and "rung-b" in message


def test_no_rungs_configured_raises_actionably():
    with pytest.raises(VlmExtractionError, match="GMI_API_KEY"):
        VlmExtractor([], client_factory=lambda b, k: None)


# ── ladder construction from env ─────────────────────────────────────────────
def test_default_ladder_env_combinations():
    assert default_ladder({}) == []
    gmi_only = default_ladder({"GMI_API_KEY": "k"})
    assert [r.model for r in gmi_only] == ["google/gemma-4-31b-it",
                                           "google/gemini-3.5-flash"]
    assert all(r.base_url == "https://api.gmi-serving.com/v1" for r in gmi_only)
    # The reasoning rung keeps the large hidden-token budget.
    assert gmi_only[1].max_tokens >= 500
    full = default_ladder({"GMI_API_KEY": "k",
                           "NEBIUS_INFERENCE_API_KEY": "n",
                           "NEBIUS_INFERENCE_BASE_URL": "https://neb.test/v1"})
    assert [r.model for r in full][-1] == "Qwen/Qwen2.5-VL-72B-Instruct"
    # Nebius rung needs BOTH values.
    assert len(default_ladder({"NEBIUS_INFERENCE_API_KEY": "n"})) == 0


# ── parsing + normalisation units ────────────────────────────────────────────
def test_extract_json_object_variants():
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Sure! {"a": 1} there you go') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json_object("no json here")
    with pytest.raises(ValueError):
        extract_json_object("[1, 2]")


def test_normalize_strips_nulls_but_keeps_hallucinated_fields_visible():
    payload = normalize_scene_payload({
        "road": {"layout": "straight", "lanes_per_direction": 1,
                 "signal": None},
        "vehicles": None,
        "gps": [1.0, 2.0],
    })
    assert "signal" not in payload["road"]  # null stripped → default applies
    assert payload["vehicles"] == []
    assert payload["gps"] == [1.0, 2.0]  # left for extra="forbid" to reject
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SceneGraph.model_validate(payload)


def test_system_prompt_teaches_the_full_vocabulary():
    prompt = build_system_prompt()
    for token in ("straight", "t_intersection", "x_intersection", "roundabout",
                  "parking_lot", "car", "van", "truck", "motorcycle", "bicycle",
                  "scratch", "dent", "crush", "stopped", "low", "moderate",
                  "high", "left_turn", "reversing", "lane_change",
                  "vehicles_come_to_rest", "claimscene/scene/v1"):
        assert token in prompt, token
    assert "12=front" in prompt and "6=rear" in prompt


# ── live smoke (paid; excluded from CI) ──────────────────────────────────────
@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GMI_API_KEY"),
                    reason="live smoke needs GMI_API_KEY")
def test_live_ladder_extracts_a_scene_from_a_synthetic_photo():
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 200), (245, 245, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 130, 320, 178], fill=(90, 90, 95))
    for x0, color in ((25, (200, 30, 30)), (180, (30, 60, 200))):
        d.rectangle([x0, 108, x0 + 115, 142], fill=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    photo = CasePhoto(filename="cars.png", data=buf.getvalue(),
                      source=PhotoSource.synthetic_generated)
    scene = VlmExtractor().extract(
        [photo], context="two cars side by side on a straight road; "
        "the red car is to the west of the blue car")
    assert 1 <= len(scene.vehicles) <= 3
