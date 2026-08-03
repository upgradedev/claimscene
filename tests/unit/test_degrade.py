"""The live-provider failure classifier (``claimscene.degrade``).

What matters here is not clever matching, it is that a visitor gets an honest
category and never the upstream text. So these tests pin two things: the
recognised shapes map to the right kind, and anything unrecognised stays
``"unknown"`` instead of being guessed into a plausible-sounding cause.
"""
from __future__ import annotations

import pytest

from claimscene.degrade import KINDS, classify


class ProviderError(Exception):
    """Stands in for the provider stack's own exception class."""


@pytest.mark.parametrize(("message", "expected"), [
    # The real one, from our sibling entry's incident log.
    ("GMICloud submit failed (402): Insufficient credits", "credit"),
    ("payment required for this account", "credit"),
    ("balance too low: insufficient balance", "credit"),
    ("HTTP 429 Too Many Requests", "rate_limit"),
    ("rate limit exceeded, retry in 30s", "rate_limit"),
    ("submit failed (401): unauthorized", "auth"),
    ("403 Forbidden", "auth"),
    ("invalid api key supplied", "auth"),
    ("read timed out after 600s", "timeout"),
    ("Timeout waiting for the generation to finish", "timeout"),
    ("submit failed (503): service unavailable", "unavailable"),
    ("connection refused", "unavailable"),
    ("something we have never seen before", "unknown"),
])
def test_classifies_known_shapes(message: str, expected: str) -> None:
    assert classify(ProviderError(message)) == expected


def test_every_result_is_in_the_closed_vocabulary() -> None:
    for message in ["402 insufficient credits", "429", "401", "timed out", "502", "???"]:
        assert classify(ProviderError(message)) in KINDS


def test_reads_the_cause_chain_not_just_the_outermost_error() -> None:
    """The status code usually lives on the innermost error, and the wrapper
    that reaches us says something bland like "pipeline step failed"."""
    try:
        try:
            raise ProviderError("submit failed (402): Insufficient credits")
        except ProviderError as inner:
            raise RuntimeError("pipeline step 'illustration' failed") from inner
    except RuntimeError as outer:
        assert classify(outer) == "credit"


def test_reads_an_implicit_context_chain_too() -> None:
    """A bare re-raise inside an ``except`` links via ``__context__``, not
    ``__cause__`` — the provider stack does both."""
    try:
        try:
            raise ProviderError("HTTP 429 rate limit")
        except ProviderError:
            raise RuntimeError("generation failed")  # noqa: B904 - implicit chain is the point
    except RuntimeError as outer:
        assert classify(outer) == "rate_limit"


def test_a_cyclic_cause_chain_terminates() -> None:
    """Defensive: a self-referencing chain must not spin forever."""
    a = ProviderError("outer")
    b = ProviderError("inner 402 insufficient credits")
    a.__cause__ = b
    b.__cause__ = a
    assert classify(a) == "credit"


def test_falls_back_to_the_exception_type_when_the_message_says_nothing() -> None:
    assert classify(TimeoutError()) == "timeout"
    assert classify(ConnectionError()) == "unavailable"
    assert classify(ValueError("")) == "unknown"


def test_never_raises_on_a_hostile_exception() -> None:
    """A classifier that blew up while handling a failure would turn an honest
    degrade into a 500 — the opposite of the point."""

    class Hostile(Exception):
        def __str__(self) -> str:
            raise RuntimeError("no string for you")

    assert classify(Hostile()) == "unknown"
