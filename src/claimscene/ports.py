"""Ports (protocols) that decouple orchestration from concrete providers.

The real adapters will wrap a hosted VLM (scene extraction), Genblaze
(illustration generation) and Backblaze B2 / S3 (storage). The fakes in
``adapters/fakes.py`` implement the same protocols with no network, so the
whole pipeline — including real SHA-256 provenance — runs offline in CI.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .case import CasePhoto
from .layout import Timeline
from .scene import SceneGraph
from .schematic import SchematicArtifacts


@runtime_checkable
class VisionExtractor(Protocol):
    """Photos + optional context → a *constrained* SceneGraph.

    Implementations must return a validated SceneGraph — free-form spatial
    output has no representation and cannot pass the model boundary.
    """

    name: str

    def extract(
        self, photos: Sequence[CasePhoto], *, context: str | None = None
    ) -> SceneGraph:
        ...


@runtime_checkable
class MediaProvider(Protocol):
    """A generative-media backend (the cinematic illustration clip)."""

    name: str

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        inputs: list[bytes] | None = None,
        params: dict | None = None,
    ) -> bytes:
        ...


@runtime_checkable
class StorageBackend(Protocol):
    """An object store (Backblaze B2 / any S3-compatible store)."""

    def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str:
        """Store ``data`` under ``key``; return a durable URL."""
        ...

    def get(self, key: str) -> bytes:
        ...

    def exists(self, key: str) -> bool:
        ...


@runtime_checkable
class Renderer(Protocol):
    """Timeline → schematic artifacts (static SVG, PNG frames, animation)."""

    name: str

    def render(self, timeline: Timeline, *, title: str | None = None) -> SchematicArtifacts:
        ...
