"""Upload guardrails for user-supplied photo bytes.

ClaimScene accepts real accident photos as multipart uploads on
``/cases/extract`` and ``/cases/render``. A bad or hostile upload must fail as a
clean 4xx at the boundary — never a 5xx, an out-of-memory, or a disguised
executable slipping into the content-addressed store. These are the pure,
framework-agnostic guards the API layer enforces *before* any bytes become a
``CasePhoto`` or reach the pipeline; :mod:`claimscene.api` maps a raised
:class:`UploadError` to an HTTP 4xx.

Shared foundation with our other entry, Cinemory (MIT): the magic-byte blocklist
and the bounded-ingest approach are the same, adapted to ClaimScene's per-case
photo uploads.
"""
from __future__ import annotations

#: Guardrails so a bad request is a clean 4xx, never a 5xx or an OOM. A claim is
#: documented from a handful of accident views (scene / damage / road); 30 is
#: well above any real case yet far below a resource-exhaustion payload.
MAX_PHOTOS = 30

#: Per-file byte cap (~15 MB) — comfortably above a high-resolution phone photo,
#: below a memory-amplification upload. Enforced per file, after the count cap.
MAX_PHOTO_BYTES = 15 * 1024 * 1024

#: Magic-byte prefixes for content that must never be accepted as a "photo":
#: executables and active/scripted markup — an upload disguised as an image.
#: Kept deliberately tight (a blocklist of dangerous types, *not* an image
#: allowlist) so legitimate/opaque photo bytes are never falsely rejected.
_DANGEROUS_MAGIC: tuple[bytes, ...] = (
    b"MZ",                # DOS/Windows PE executable
    b"\x7fELF",           # ELF executable
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit (little-endian)
    b"#!",                # script shebang
    b"<?php",             # PHP source
)

#: Case-insensitive markup markers checked against a leading, whitespace-stripped
#: window (active/scriptable HTML masquerading as an image).
_DANGEROUS_MARKUP: tuple[bytes, ...] = (b"<script", b"<!doctype html", b"<html")


class UploadError(ValueError):
    """Raised for an upload that violates the ingest guardrails (maps to 4xx)."""


def reject_dangerous_bytes(data: bytes) -> None:
    """Raise :class:`UploadError` if ``data`` is a disguised executable/script.

    Photos are opaque binary, so only content whose leading bytes positively
    identify an executable or active markup payload is rejected — never merely
    "not a known image", so ambiguous/opaque photo bytes still pass.
    """
    if data.startswith(_DANGEROUS_MAGIC):
        raise UploadError("uploaded content is an executable, not a photo")
    head = data[:64].lstrip().lower()
    if head.startswith(_DANGEROUS_MARKUP):
        raise UploadError("uploaded content is active markup, not a photo")
