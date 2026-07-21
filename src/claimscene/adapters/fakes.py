"""Deterministic offline fakes for every port — CI needs zero credentials.

FakeVisionExtractor derives a *fixture* SceneGraph from the input photo
hashes (same photos → same scene, always valid against the constrained
vocabulary). FakeMediaProvider hashes the request into deterministic bytes.
InMemoryStorage mirrors the real B2 adapter's index surface exactly.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from ..case import CasePhoto
from ..scene import (
    DamageSeverity,
    DamageZone,
    Impact,
    Movement,
    Road,
    SceneGraph,
    SequenceEvent,
    Vehicle,
)


def _scene_rear_end() -> SceneGraph:
    return SceneGraph(
        road=Road(layout="straight", lanes_per_direction=1, signal="traffic_light"),
        vehicles=[
            Vehicle(id="veh_a", kind="car", color="blue", damage=[
                DamageZone(clock_position=6, severity=DamageSeverity.crush,
                           note="rear bumper and trunk deformation"),
            ]),
            Vehicle(id="veh_b", kind="car", color="red", damage=[
                DamageZone(clock_position=12, severity=DamageSeverity.dent,
                           note="front bumper and hood"),
            ]),
        ],
        movements=[
            Movement(vehicle_id="veh_a", approach="N", maneuver="straight",
                     speed_band="stopped"),
            Movement(vehicle_id="veh_b", approach="N", maneuver="straight",
                     speed_band="moderate"),
        ],
        impacts=[
            Impact(vehicle_id="veh_a", clock_position=6),
            Impact(vehicle_id="veh_b", clock_position=12),
        ],
        sequence=[SequenceEvent.vehicles_approach, SequenceEvent.braking,
                  SequenceEvent.impact, SequenceEvent.vehicles_come_to_rest],
    )


def _scene_left_cross() -> SceneGraph:
    return SceneGraph(
        road=Road(layout="x_intersection", lanes_per_direction=1,
                  signal="traffic_light"),
        vehicles=[
            Vehicle(id="veh_a", kind="car", color="silver", damage=[
                DamageZone(clock_position=2, severity=DamageSeverity.crush,
                           note="right front quarter"),
            ]),
            Vehicle(id="veh_b", kind="van", color="green", damage=[
                DamageZone(clock_position=12, severity=DamageSeverity.dent),
            ]),
        ],
        movements=[
            Movement(vehicle_id="veh_a", approach="N", maneuver="left_turn",
                     speed_band="low"),
            Movement(vehicle_id="veh_b", approach="S", maneuver="straight",
                     speed_band="moderate"),
        ],
        impacts=[
            Impact(vehicle_id="veh_a", clock_position=2),
            Impact(vehicle_id="veh_b", clock_position=12),
        ],
        sequence=[SequenceEvent.vehicles_approach, SequenceEvent.evasive_steering,
                  SequenceEvent.impact, SequenceEvent.vehicles_come_to_rest],
    )


def _scene_parking_reverse() -> SceneGraph:
    return SceneGraph(
        road=Road(layout="parking_lot", lanes_per_direction=1, signal="none"),
        vehicles=[
            Vehicle(id="veh_a", kind="van", color="white", damage=[
                DamageZone(clock_position=6, severity=DamageSeverity.dent,
                           note="rear door panel"),
            ]),
            Vehicle(id="veh_b", kind="car", color="black", damage=[
                DamageZone(clock_position=9, severity=DamageSeverity.scratch),
            ]),
        ],
        movements=[
            Movement(vehicle_id="veh_a", approach="N", maneuver="reversing",
                     speed_band="low"),
            Movement(vehicle_id="veh_b", approach="N", maneuver="parked",
                     speed_band="stopped"),
        ],
        impacts=[
            Impact(vehicle_id="veh_b", clock_position=9),
            Impact(vehicle_id="veh_a", clock_position=6),
        ],
        sequence=[SequenceEvent.impact, SequenceEvent.vehicles_come_to_rest],
    )


def _scene_roundabout_sideswipe() -> SceneGraph:
    return SceneGraph(
        road=Road(layout="roundabout", lanes_per_direction=1, signal="yield"),
        vehicles=[
            Vehicle(id="veh_a", kind="car", color="yellow", damage=[
                DamageZone(clock_position=3, severity=DamageSeverity.scratch,
                           note="passenger-side doors"),
            ]),
            Vehicle(id="veh_b", kind="motorcycle", color="black", damage=[
                DamageZone(clock_position=9, severity=DamageSeverity.scratch),
            ]),
        ],
        movements=[
            Movement(vehicle_id="veh_a", approach="N", maneuver="straight",
                     speed_band="low"),
            Movement(vehicle_id="veh_b", approach="W", maneuver="straight",
                     speed_band="low"),
        ],
        impacts=[
            Impact(vehicle_id="veh_a", clock_position=3),
            Impact(vehicle_id="veh_b", clock_position=9),
        ],
        sequence=[SequenceEvent.vehicles_approach, SequenceEvent.impact,
                  SequenceEvent.vehicles_come_to_rest],
    )


_FIXTURES = [
    ("rear_end", _scene_rear_end),
    ("left_cross", _scene_left_cross),
    ("parking_reverse", _scene_parking_reverse),
    ("roundabout_sideswipe", _scene_roundabout_sideswipe),
]


class FakeVisionExtractor:
    """Deterministic SceneGraph fixtures selected by input content hashes."""

    name = "fake-vision"

    def extract(self, photos: Sequence[CasePhoto], *,
                context: str | None = None) -> SceneGraph:
        digest = hashlib.sha256()
        for photo in photos:
            digest.update(hashlib.sha256(photo.data).digest())
        if context:
            digest.update(context.encode("utf-8"))
        fixture_name, builder = _FIXTURES[int(digest.hexdigest()[:8], 16) % len(_FIXTURES)]
        scene = builder()
        scene.confidence_notes.append(
            f"offline deterministic extraction (fixture '{fixture_name}') "
            "derived from input photo hashes; no VLM was called"
        )
        return scene


class FakeMediaProvider:
    """Deterministic bytes from a hash of the full generation request."""

    name = "fake-media"

    def generate(self, *, model: str, prompt: str, modality: str = "video",
                 inputs: list[bytes] | None = None,
                 params: dict | None = None) -> bytes:
        digest = hashlib.sha256()
        digest.update(modality.encode("utf-8"))
        digest.update(model.encode("utf-8"))
        digest.update(prompt.encode("utf-8"))
        for blob in inputs or []:
            digest.update(hashlib.sha256(blob).digest())
        digest.update(json.dumps(params or {}, sort_keys=True).encode("utf-8"))
        seed = digest.digest()
        return b"CLAIMSCENE-FAKE-MEDIA/" + digest.hexdigest().encode() + b"/" + seed * 96


class InMemoryStorage:
    """Offline object store mimicking the B2 adapter's exact index surface."""

    def __init__(self, bucket: str = "claimscene-demo") -> None:
        self.bucket = bucket
        self._objects: dict[str, bytes] = {}
        self.index: list[dict] = []

    def put(self, key: str, data: bytes, *,
            content_type: str = "application/octet-stream") -> str:
        self._objects[key] = data
        self.index.append({"key": key, "size": len(data), "content_type": content_type})
        return f"b2://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        if key not in self._objects:
            raise KeyError(key)
        return self._objects[key]

    def exists(self, key: str) -> bool:
        return key in self._objects

    def reload_index(self) -> list[dict]:
        return self.index

    def index_jsonl(self) -> str:
        """Serialise the asset catalogue (one JSON row per stored object)."""
        return "\n".join(json.dumps(row, sort_keys=True) for row in self.index)
