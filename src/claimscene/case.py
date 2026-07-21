"""Case-level input models: photos with per-input source attribution.

Every input photo carries its provenance *at ingest time* — where it came
from (``source``), and optional ``attribution`` / ``license`` strings — so
the sealed manifest can record honest per-input origins, not just hashes.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PhotoRole(str, Enum):
    scene_photo = "scene_photo"
    damage_photo = "damage_photo"
    road_photo = "road_photo"


class PhotoSource(str, Enum):
    user_upload = "user_upload"
    staged_demo = "staged_demo"
    public_domain = "public_domain"
    licensed = "licensed"
    synthetic_generated = "synthetic_generated"


class CasePhoto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    data: bytes
    media_type: str = "image/png"
    role: PhotoRole = PhotoRole.scene_photo
    source: PhotoSource
    attribution: str | None = None
    license: str | None = None


class CaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=64)
    photos: list[CasePhoto] = Field(min_length=1)
    context: str | None = None
    illustration_model: str = "Kling-v3-I2V"
