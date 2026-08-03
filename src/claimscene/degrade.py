"""Classify a live media-provider failure into a small, closed vocabulary.

Why this module exists
----------------------
When the live illustration provider fails, ``claimscene.api._run_render``
already degrades honestly: it re-seals the SAME reviewed scene with the
offline provider, so the case still completes and the manifest still records
``illustration.degraded: true``. What the visitor saw, though, was only a long
wait followed by a placeholder — the *kind* of failure lived in Cloud Logging.
Our sibling entry Cinemory hit exactly that: the shared GMI account ran out of
credits, the backend logged ``GMICloud submit failed (402): Insufficient
credits``, and the person on the page had no way to know that from the app.

So this maps an exception to ONE of :data:`KINDS`. Two rules make it safe to
put that value in an HTTP response and in the sealed manifest:

1. **Only the kind crosses the wire.** The strings matched below come from
   upstream exception text, which can embed request URLs, model names, account
   identifiers, or a raw provider error body. None of that is returned — the
   caller gets a single lowercase token from a fixed set, and the frontend
   turns that token into a sentence a claimant can read.
2. **The real reason stays in the logs.** ``_run_render`` logs the full
   exception (``_log.exception``) with the kind alongside it, so an operator
   greps one line and gets both the plain-language category the visitor saw
   and the upstream detail they did not.

The matching is deliberately text-based rather than typed: the provider stack
(Genblaze → GMI Cloud → the underlying HTTP client) raises several unrelated
exception classes and folds the upstream status into the message. Walking the
``__cause__``/``__context__`` chain and looking for a status code or a
well-known phrase is what actually survives that. An unrecognised failure is
``"unknown"``, never a guess — the UI says "something went wrong at their end"
rather than inventing a cause.
"""
from __future__ import annotations

import re

#: The closed vocabulary. Every value here has a matching plain-language
#: sentence in the web client (``frontend/src/lib/degrade.ts``); adding a kind
#: means adding it there too, and the frontend falls back to the "unknown"
#: wording for anything it does not recognise.
KINDS = ("credit", "auth", "rate_limit", "timeout", "unavailable", "unknown")

#: How many links of the ``__cause__``/``__context__`` chain are inspected.
#: Bounded so a self-referencing or pathologically deep chain cannot spin.
_MAX_CHAIN = 8

# Ordered most-specific first: 402 must win over a generic "payment"/"auth"
# reading, and 429 over the 5xx/connection bucket.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credit", re.compile(
        r"\b402\b|insufficient\s+(credit|balance|fund)|out\s+of\s+credit"
        r"|payment\s+required|billing|top\s*[-_ ]?up|no\s+credits")),
    ("rate_limit", re.compile(r"\b429\b|rate[\s_-]?limit|too\s+many\s+requests|throttl")),
    ("auth", re.compile(
        r"\b40[13]\b|unauthori[sz]ed|forbidden|invalid\s+api[\s_-]?key"
        r"|authentication\s+fail|missing\s+api[\s_-]?key|api[\s_-]?key\s+not")),
    ("timeout", re.compile(r"timeout|timed\s+out|deadline\s+exceeded")),
    ("unavailable", re.compile(
        r"\b5\d\d\b|service\s+unavailable|bad\s+gateway|connection\s+(refused|reset|error)"
        r"|temporarily\s+unavailable|cannot\s+connect|name\s+resolution|unreachable")),
)


def _chain_text(exc: BaseException) -> str:
    """Lowercased ``ClassName: message`` for the exception and its causes.

    Both ``__cause__`` (explicit ``raise ... from``) and ``__context__``
    (implicit re-raise) are followed, because the provider stack uses both,
    and the status code is often only present on the innermost one. Cycles are
    impossible to follow twice: visited exceptions are tracked by identity.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(parts) < _MAX_CHAIN and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts).lower()


def classify(exc: BaseException) -> str:
    """One of :data:`KINDS` describing why the live provider could not deliver.

    Never raises: a classifier that blew up while handling a failure would
    turn an honest degrade into a 500, which is the exact opposite of what
    this codebase promises.
    """
    try:
        text = _chain_text(exc)
    except Exception:  # pragma: no cover - defensive; str() of a hostile repr
        return "unknown"
    for kind, pattern in _RULES:
        if pattern.search(text):
            return kind
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "unavailable"
    return "unknown"
