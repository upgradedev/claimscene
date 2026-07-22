"""Shared fixtures: tiny real PNG bytes, photos, scenes, and a fake S3 seam."""
from __future__ import annotations

import pytest

from claimscene.case import CasePhoto, PhotoRole, PhotoSource

# A tiny, valid 1x1 PNG so ingest paths see genuine image bytes.
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc``\x00\x00\x00\x04"
    b"\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def png_1x1() -> bytes:
    return PNG_1x1


@pytest.fixture
def photos() -> list[CasePhoto]:
    return [
        CasePhoto(filename="scene1.png", data=PNG_1x1 + b"a", role=PhotoRole.scene_photo,
                  source=PhotoSource.staged_demo),
        CasePhoto(filename="damage1.png", data=PNG_1x1 + b"b", role=PhotoRole.damage_photo,
                  source=PhotoSource.staged_demo),
        CasePhoto(filename="road1.png", data=PNG_1x1 + b"c", role=PhotoRole.road_photo,
                  source=PhotoSource.public_domain, attribution="Municipal survey archive",
                  license="CC0-1.0"),
    ]


class Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3:
    """Minimal S3-compatible stub for the B2 adapter's client seam."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.store[(Bucket, Key)] = Body
        return {}

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.store:
            raise KeyError(Key)
        return {"Body": Body_(self.store[(Bucket, Key)])}


# Alias so both names read naturally at call sites.
Body_ = Body


@pytest.fixture
def fake_s3() -> FakeS3:
    return FakeS3()


@pytest.fixture
def clean_b2_env(monkeypatch) -> None:
    for name in ("B2_BUCKET_NAME", "B2_S3_ENDPOINT", "B2_ENDPOINT_URL",
                 "B2_APPLICATION_KEY_ID", "B2_KEY_ID", "B2_APPLICATION_KEY",
                 "B2_APP_KEY", "B2_KEY_PREFIX", "B2_PREFIX", "B2_REGION",
                 "CLAIMSCENE_MODE", "GMI_API_KEY", "GMI_SERVING_BASE_URL",
                 "NEBIUS_INFERENCE_API_KEY", "NEBIUS_INFERENCE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
