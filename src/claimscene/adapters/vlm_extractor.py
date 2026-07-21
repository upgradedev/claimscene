"""Live VLM scene extractor — photos → constrained SceneGraph, over a model ladder.

The extractor speaks the OpenAI chat-completions wire shape (GMI Cloud's
serving endpoint and Nebius inference are both OpenAI-compatible), sending
photos as base64 ``data:`` image_url blocks. The prompt embeds the FULL
closed vocabulary from :mod:`claimscene.scene` (generated from the enums, so
it cannot drift) and demands strict JSON. The reply is normalised with the
null-safe ``value or default`` pattern, then validated by the SceneGraph
model — the anti-hallucination gate. On validation failure the errors are
fed back for exactly one repair round-trip per rung.

Ladder (probe-verified 2026-07-21):
  1. GMI ``google/gemma-4-31b-it``       — fast, cheap, clean JSON (primary)
  2. GMI ``google/gemini-3.5-flash``     — reasoning model; needs a large
                                            max_tokens budget (hidden
                                            reasoning tokens count against it)
  3. Nebius ``Qwen/Qwen2.5-VL-72B-Instruct`` — independent-provider fallback

A rung is retried once with backoff on retryable transport failures
(429 / 5xx / timeout / connection); anything else falls through to the next
rung. The OpenAI client import is lazy so offline CI needs neither the
package nor a key; unit tests inject a canned ``client_factory``.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from ..case import CasePhoto
from ..scene import (
    Approach,
    DamageSeverity,
    Maneuver,
    RoadLayout,
    SceneGraph,
    SequenceEvent,
    Signal,
    SpeedBand,
    VehicleColor,
    VehicleKind,
)

_log = logging.getLogger("claimscene.vlm")

#: GMI's OpenAI-compatible *serving* endpoint (distinct from the GMICloud
#: request-queue API the Genblaze provider uses).
GMI_SERVING_BASE_URL_DEFAULT = "https://api.gmi-serving.com/v1"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRYABLE_EXC_NAMES = {
    "APITimeoutError", "APIConnectionError", "Timeout", "ConnectionError",
    "ReadTimeout", "ConnectTimeout",
}


class VlmExtractionError(RuntimeError):
    """Every rung of the VLM ladder failed to produce a valid SceneGraph."""


@dataclass(frozen=True)
class VlmRung:
    """One model on the fallback ladder (an OpenAI-compatible endpoint)."""

    name: str
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 1400
    temperature: float = 0.0


def default_ladder(env: dict | None = None) -> list[VlmRung]:
    """Build the ladder from the environment; rungs without keys are skipped."""
    import os

    env = env if env is not None else dict(os.environ)
    rungs: list[VlmRung] = []
    gmi_key = env.get("GMI_API_KEY")
    gmi_base = env.get("GMI_SERVING_BASE_URL") or GMI_SERVING_BASE_URL_DEFAULT
    if gmi_key:
        rungs.append(VlmRung(name="gmi:google/gemma-4-31b-it", base_url=gmi_base,
                             api_key=gmi_key, model="google/gemma-4-31b-it",
                             max_tokens=1400))
        # Reasoning model: hidden reasoning tokens count against max_tokens,
        # so the budget must be far above the visible JSON size.
        rungs.append(VlmRung(name="gmi:google/gemini-3.5-flash", base_url=gmi_base,
                             api_key=gmi_key, model="google/gemini-3.5-flash",
                             max_tokens=3000))
    nebius_key = env.get("NEBIUS_INFERENCE_API_KEY")
    nebius_base = env.get("NEBIUS_INFERENCE_BASE_URL")
    if nebius_key and nebius_base:
        rungs.append(VlmRung(
            name="nebius:qwen2.5-vl-72b", base_url=nebius_base, api_key=nebius_key,
            model=env.get("VISION_MODEL") or "Qwen/Qwen2.5-VL-72B-Instruct",
            max_tokens=1400))
    return rungs


# ── prompt (vocabulary generated from the enums — cannot drift) ──────────────
def _enum_values(enum_cls) -> str:
    return " | ".join(e.value for e in enum_cls)


_MINI_EXAMPLE = json.dumps({
    "schema": "claimscene/scene/v1",
    "road": {"layout": "straight", "lanes_per_direction": 1,
             "signal": "traffic_light"},
    "vehicles": [
        {"id": "veh_a", "kind": "car", "color": "blue",
         "damage": [{"clock_position": 6, "severity": "crush",
                     "note": "rear bumper crushed"}]},
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
    "sequence": ["vehicles_approach", "braking", "impact",
                 "vehicles_come_to_rest"],
}, separators=(",", ":"))


def build_system_prompt() -> str:
    """The constrained-vocabulary extraction instructions."""
    return (
        "You are the ClaimScene scene extractor. You are given photos of a road "
        "traffic incident (they may be staged miniature/toy dioramas or real "
        "scenes) plus an optional context note. Describe the incident ONLY in "
        "the closed vocabulary below and return ONLY one JSON object — no "
        "markdown fences, no commentary, no extra fields, no coordinates.\n\n"
        "JSON shape (schema claimscene/scene/v1):\n"
        '{"schema":"claimscene/scene/v1","road":{...},"vehicles":[...],'
        '"movements":[...],"impacts":[...],"sequence":[...],'
        '"confidence_notes":[...]}\n\n'
        "Closed vocabulary (any other value is invalid):\n"
        f"- road.layout: {_enum_values(RoadLayout)}\n"
        "- road.lanes_per_direction: integer 1..3\n"
        f"- road.signal: {_enum_values(Signal)}\n"
        '- vehicles[]: {"id","kind","color","damage":[...]} — id is a short '
        'label like "veh_a"\n'
        f"- vehicles[].kind: {_enum_values(VehicleKind)}\n"
        f"- vehicles[].color: {_enum_values(VehicleColor)} (pick the nearest)\n"
        '- vehicles[].damage[]: {"clock_position":1..12,"severity",'
        '"note"(optional)} — clock position on THAT vehicle\'s own body: '
        "12=front, 3=right side, 6=rear, 9=left side\n"
        f"- vehicles[].damage[].severity: {_enum_values(DamageSeverity)}\n"
        '- movements[]: {"vehicle_id","approach","maneuver","speed_band"} — '
        "at most ONE movement per vehicle\n"
        f"- movements[].approach: {_enum_values(Approach)} (compass direction "
        "the vehicle came FROM; use the context note to resolve compass "
        "directions when given)\n"
        f"- movements[].maneuver: {_enum_values(Maneuver)}\n"
        f"- movements[].speed_band: {_enum_values(SpeedBand)}\n"
        '- impacts[]: {"vehicle_id","clock_position":1..12} — where THAT '
        "vehicle was struck\n"
        f"- sequence[]: ordered events from: {_enum_values(SequenceEvent)}\n"
        "- confidence_notes[]: short strings for anything uncertain\n\n"
        "Rules: every vehicle_id in movements/impacts must exist in vehicles; "
        "vehicle ids must be unique; damage location must match the impact "
        "story; when unsure between two enum values, pick the closest and add "
        "a confidence note. Speed bands, never numbers. No positions, no "
        "distances, no free-form geometry.\n\n"
        f"Example output for a rear-end collision:\n{_MINI_EXAMPLE}"
    )


def _photo_content_block(photo: CasePhoto, *, max_dim: int = 1024,
                         quality: int = 85) -> dict:
    """One image_url content block: photo bytes → bounded base64 JPEG."""
    data, media_type = photo.data, photo.media_type
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(photo.data)).convert("RGB")
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data, media_type = buf.getvalue(), "image/jpeg"
    except Exception:  # unreadable → send the original bytes as declared
        pass
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{b64}"}}


def build_user_content(photos: Sequence[CasePhoto],
                       context: str | None = None) -> list[dict]:
    content: list[dict] = []
    for i, photo in enumerate(photos, start=1):
        content.append({"type": "text",
                        "text": f"Photo {i} ({photo.role.value}):"})
        content.append(_photo_content_block(photo))
    if context:
        content.append({"type": "text", "text": f"Context note: {context}"})
    content.append({"type": "text",
                    "text": "Return the claimscene/scene/v1 JSON object now."})
    return content


# ── reply parsing + null-safe normalisation (ADR-003) ────────────────────────
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> dict:
    """Pull the JSON object out of a model reply (fences tolerated)."""
    text = (text or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("reply contains no JSON object")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("reply JSON is not an object")
    return parsed


def _strip_none(value):
    """Recursively drop ``None`` values so Pydantic defaults apply.

    ``data.get(key, default)`` misses present-but-null keys (ADR-003); the
    LLM frequently emits ``"note": null``. Stripping nulls lets every
    optional field fall back to its explicit ``= None``/default while keeping
    genuinely wrong values visible to validation (we never invent data).
    """
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value if v is not None]
    return value


def normalize_scene_payload(raw: dict) -> dict:
    """Shape the reply for SceneGraph validation without inventing facts."""
    raw = _strip_none(raw)
    payload = {
        "schema": "claimscene/scene/v1",
        "road": raw.get("road") or {},
        "vehicles": raw.get("vehicles") or [],
        "movements": raw.get("movements") or [],
        "impacts": raw.get("impacts") or [],
        "sequence": raw.get("sequence") or [],
        "confidence_notes": [str(n) for n in (raw.get("confidence_notes") or [])],
    }
    # Anything else the model added stays — extra="forbid" must see and
    # reject hallucinated fields (that is the gate, not silent dropping).
    for key, value in raw.items():
        if key not in payload and key != "schema":
            payload[key] = value
    return payload


def _validation_feedback(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        rows = [f"- {'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()[:12]]
        return "\n".join(rows)
    return str(exc)


# ── transport ────────────────────────────────────────────────────────────────
def _default_client_factory(base_url: str, api_key: str):
    """Real OpenAI-compatible client (lazy import; [live] extra)."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - import guard
        raise VlmExtractionError(
            "the live VLM extractor needs the openai package: "
            "pip install claimscene[live]"
        ) from exc
    return OpenAI(base_url=base_url, api_key=api_key, timeout=120.0,
                  max_retries=0)


def _status_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status if isinstance(status, int) else None


def _is_retryable(exc: Exception) -> bool:
    if _status_of(exc) in _RETRYABLE_STATUS:
        return True
    return type(exc).__name__ in _RETRYABLE_EXC_NAMES


class VlmExtractor:
    """Photos + context → validated SceneGraph via the VLM fallback ladder."""

    name = "vlm-ladder"

    def __init__(
        self,
        rungs: Sequence[VlmRung] | None = None,
        *,
        client_factory: Callable[[str, str], object] | None = None,
        attempts_per_rung: int = 2,
        backoff_s: float = 2.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rungs = list(rungs) if rungs is not None else default_ladder()
        if not self.rungs:
            raise VlmExtractionError(
                "no VLM ladder rungs are configured (set GMI_API_KEY and/or "
                "NEBIUS_INFERENCE_BASE_URL + NEBIUS_INFERENCE_API_KEY)"
            )
        self._client_factory = client_factory or _default_client_factory
        self._clients: dict[tuple[str, str], object] = {}
        self.attempts_per_rung = max(1, attempts_per_rung)
        self.backoff_s = backoff_s
        self._sleep = sleeper
        #: Name of the rung that produced the last successful extraction.
        self.last_rung: str | None = None
        #: Per-call token usage rows (model, prompt/completion tokens) when
        #: the endpoint reports them — feeds the eval harness spend ledger.
        self.usage_log: list[dict] = []

    # -- transport ------------------------------------------------------------
    def _client(self, rung: VlmRung):
        key = (rung.base_url, rung.api_key)
        if key not in self._clients:
            self._clients[key] = self._client_factory(rung.base_url, rung.api_key)
        return self._clients[key]

    def _chat(self, rung: VlmRung, messages: list[dict]) -> str:
        """One chat call with bounded in-rung retry on retryable failures."""
        last_exc: Exception | None = None
        for attempt in range(self.attempts_per_rung):
            try:
                resp = self._client(rung).chat.completions.create(
                    model=rung.model, messages=messages,
                    max_tokens=rung.max_tokens, temperature=rung.temperature,
                )
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    self.usage_log.append({
                        "model": rung.model,
                        "prompt_tokens": getattr(usage, "prompt_tokens", None),
                        "completion_tokens": getattr(usage, "completion_tokens", None),
                    })
                text = (resp.choices[0].message.content or "").strip()
                if not text:
                    raise ValueError("empty completion")
                return text
            except Exception as exc:
                last_exc = exc
                if _is_retryable(exc) and attempt + 1 < self.attempts_per_rung:
                    self._sleep(self.backoff_s * (attempt + 1))
                    continue
                raise
        raise last_exc  # pragma: no cover - loop always raises or returns

    # -- extraction -----------------------------------------------------------
    def _validate_reply(self, reply: str) -> SceneGraph:
        payload = normalize_scene_payload(extract_json_object(reply))
        return SceneGraph.model_validate(payload)

    def extract(self, photos: Sequence[CasePhoto], *,
                context: str | None = None) -> SceneGraph:
        system = {"role": "system", "content": build_system_prompt()}
        user = {"role": "user", "content": build_user_content(photos, context)}
        failures: list[str] = []

        for rung in self.rungs:
            try:
                reply = self._chat(rung, [system, user])
            except Exception as exc:
                _log.warning("VLM rung %s transport failure: %s", rung.name, exc)
                failures.append(f"{rung.name}: transport: {exc}")
                continue

            try:
                scene = self._validate_reply(reply)
            except Exception as exc:
                # One repair round-trip: feed the validator's complaints back.
                _log.warning("VLM rung %s invalid reply (%s); repairing",
                             rung.name, type(exc).__name__)
                repair = [
                    system, user,
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": (
                        "Your JSON was rejected by the claimscene/scene/v1 "
                        "validator:\n" + _validation_feedback(exc) +
                        "\nReturn the corrected JSON object only — same "
                        "closed vocabulary, no other text."
                    )},
                ]
                try:
                    scene = self._validate_reply(self._chat(rung, repair))
                except Exception as repair_exc:
                    failures.append(f"{rung.name}: invalid after repair: "
                                    f"{repair_exc}")
                    continue

            self.last_rung = rung.name
            scene.confidence_notes.append(
                f"scene extracted by live VLM ladder rung '{rung.name}' "
                f"(model {rung.model}); constrained-vocabulary validation passed"
            )
            return scene

        raise VlmExtractionError(
            "every VLM ladder rung failed: " + "; ".join(failures)
        )
