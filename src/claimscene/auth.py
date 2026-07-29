"""Firebase ID-token verification — no Firebase Admin SDK, no service-account
secret.

A Firebase ID token is an RS256 JWT signed by Google. It can be verified with
nothing more than Google's *public* signing certificates plus the standard
``iss``/``aud``/``exp`` claim checks — no Admin SDK, no service-account key,
nothing new to keep secret. This module is the security root of ClaimScene's
coming multitenancy: the tenant id (the token's ``sub``, Firebase's ``uid``)
must be derived ONLY from a cryptographically verified token, never trusted
from a client-supplied field (that would be an IDOR).

Ported from our other MIT entry, Cinemory, which pioneered this pattern (and
shares the B2 storage + provenance foundation this app also builds on — see
``keys.py`` / ``adapters/b2_storage.py``). This module is intentionally
standalone — nothing else in ``claimscene`` imports it yet, and it does not
touch ``api.py``. A later change wires ``uid_from_authorization`` into the API
so an optional ``Authorization: Bearer <token>`` header resolves a tenant;
this PR builds and adversarially tests the primitive in isolation.

Algorithm safety (the classic RS256/HS256 "algorithm confusion" attack): the
token's own ``alg`` header is NEVER trusted to pick the verification
algorithm. ``algorithms=["RS256"]`` is hardcoded into the ``jwt.decode`` call,
and the header's ``alg`` is independently checked against that same hardcoded
value *before* any key material is even looked up — so neither ``alg: none``
nor an HS256 token forged with the RSA public key as an HMAC "secret" can ever
reach the signature check.

``FIREBASE_PROJECT_ID`` (the env var ``project_id`` defaults from) is a
PUBLIC value — the same Firebase/GCP project id baked into client-side
Firebase config and visible in any browser's network tab. It is not a secret,
unlike a service-account key.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping

import jwt
from cryptography.x509 import load_pem_x509_certificate

#: Google's public signing certificates for Firebase/Google Identity Platform
#: ID tokens — keyed by ``kid``, PEM-encoded x509 certs (NOT a JWKS document).
#: See https://firebase.google.com/docs/auth/admin/verify-id-tokens
#: ("Retrieve the current set of public keys ...").
GOOGLE_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)

#: The only algorithm a Firebase ID token is ever signed with. Hardcoded and
#: never derived from caller input or the token itself — see the module
#: docstring's algorithm-confusion note.
ALLOWED_ALGORITHMS = ("RS256",)

#: Tolerance for clock skew between this host and Google's token issuer,
#: applied to every time-based claim: ``exp``/``nbf`` via PyJWT's ``leeway``
#: kwarg, ``iat``/``auth_time`` manually (PyJWT does not validate either).
CLOCK_SKEW_LEEWAY_SECONDS = 60

#: Env var ``project_id`` defaults from when not passed explicitly.
PROJECT_ID_ENV_VAR = "FIREBASE_PROJECT_ID"


class AuthError(ValueError):
    """Raised when a Firebase ID token fails verification, for any reason."""


# ── Google certs (default key_source) — cached, network only when stale ────
# The test suite never exercises the live fetch: it injects its own
# ``key_source`` backed by a locally generated RSA keypair, so verification
# logic runs fully offline in CI (see tests/unit/test_auth.py). The fetch
# itself mirrors this codebase's other lazy-import / injectable-transport
# seams for the same reason (e.g. ``claimscene.adapters.b2_storage``'s boto3
# client, ``claimscene.adapters.vlm_extractor``'s HTTP client) — its
# real-network lines are excluded from the coverage gate the same way those
# real-path-only branches are.

_certs_cache_lock = threading.Lock()
_certs_cache: dict[str, object] = {"certs": None, "expires_at": 0.0}
_DEFAULT_CACHE_TTL_SECONDS = 3600.0


def _parse_cache_max_age(cache_control: str | None) -> float | None:
    """Extract ``max-age`` (seconds) from a ``Cache-Control`` header value.

    Returns ``None`` when absent, malformed, or negative — callers then fall
    back to a fixed default TTL.
    """
    if not cache_control:
        return None
    for directive in cache_control.split(","):
        name, _, value = directive.strip().partition("=")
        value = value.strip()
        if name.strip().lower() == "max-age" and value.lstrip("-").isdigit():
            age = float(value)
            return age if age >= 0 else None
    return None


def _default_key_source() -> Mapping[str, str]:
    """Return Google's current certs as ``{kid: pem_cert}``, cached in-process.

    Honours the live response's ``Cache-Control: max-age`` when present
    (falls back to a fixed TTL otherwise) so a long-running process does not
    refetch on every call.
    """
    now = time.time()
    with _certs_cache_lock:
        cached = _certs_cache["certs"]
        fresh = cached is not None and now < _certs_cache["expires_at"]
    if fresh:
        return cached  # type: ignore[return-value]
    return _refresh_certs_cache(now)


def _refresh_certs_cache(now: float) -> Mapping[str, str]:  # pragma: no cover - real network path
    """Fetch Google's cert bundle over HTTPS and repopulate the cache.

    Never exercised by the test suite (which always injects an offline
    ``key_source``) — this is the one real-network function in the module,
    excluded from the coverage gate the same way this codebase's other
    real-path-only branches are.
    """
    request = urllib.request.Request(GOOGLE_CERTS_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read()
        ttl = _parse_cache_max_age(response.headers.get("Cache-Control"))
    certs = json.loads(body)
    with _certs_cache_lock:
        _certs_cache["certs"] = certs
        _certs_cache["expires_at"] = now + (ttl or _DEFAULT_CACHE_TTL_SECONDS)
    return certs


# ── Verification ─────────────────────────────────────────────────────────


def verify_firebase_id_token(
    token: str,
    *,
    project_id: str | None = None,
    key_source: Callable[[], Mapping[str, str]] | None = None,
) -> str:
    """Verify a Firebase ID token and return its ``uid`` (the ``sub`` claim).

    Every check below is mandatory; ANY failure raises :class:`AuthError` —
    there is no partial-success result.

    - Signature: RS256 against Google's public cert for the token's ``kid``.
    - ``iss == "https://securetoken.google.com/<project_id>"``.
    - ``aud == project_id``.
    - ``exp`` in the future, ``iat`` in the past, ``auth_time`` present and in
      the past (a small clock-skew allowance applies to all three — see
      :data:`CLOCK_SKEW_LEEWAY_SECONDS`).
    - ``sub`` present and non-empty (this is the returned ``uid``).
    - Algorithm: only ``RS256`` is ever accepted — see the module docstring.

    Args:
        token: The raw JWT (the ``Authorization: Bearer <token>`` value minus
            the ``Bearer`` scheme).
        project_id: Your Firebase/GCP project id — a PUBLIC value, not a
            secret. Defaults to the ``FIREBASE_PROJECT_ID`` env var
            (:data:`PROJECT_ID_ENV_VAR`) when omitted.
        key_source: Zero-arg callable returning Google's current certs as
            ``{kid: pem_cert}``. Defaults to :func:`_default_key_source` (a
            live, cached fetch). Inject a fake here to verify tokens signed by
            a locally generated keypair with no network — see
            ``tests/unit/test_auth.py``.

    Returns:
        The verified ``uid``.

    Raises:
        AuthError: ``project_id`` cannot be resolved, or the token is
            missing/malformed/expired/mis-signed/mis-issued/mis-audienced, or
            any required claim is missing or fails its check.
    """
    resolved_project_id = project_id or os.environ.get(PROJECT_ID_ENV_VAR)
    if not resolved_project_id:
        raise AuthError(f"no project_id given and {PROJECT_ID_ENV_VAR} is not set")
    if not token or not isinstance(token, str):
        raise AuthError("token is empty")

    source = key_source or _default_key_source

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError(f"malformed token: {exc}") from exc

    # Algorithm-confusion defence: the token's ``alg`` is compared against a
    # hardcoded allow-list *before* any key material is looked up, and the
    # same hardcoded list (never the header's value) is what gets passed to
    # jwt.decode below. This blocks both `alg: none` and an HS256 token
    # forged using the RSA public key bytes as an HMAC secret.
    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        raise AuthError(f"unsupported algorithm {alg!r} (only RS256 is accepted)")

    kid = header.get("kid")
    if not kid:
        raise AuthError("token header is missing kid")

    try:
        certs = source()
    except Exception as exc:
        raise AuthError(f"could not obtain signing keys: {exc}") from exc

    cert_pem = certs.get(kid) if certs else None
    if not cert_pem:
        raise AuthError(f"unknown signing key id {kid!r}")

    try:
        public_key = load_pem_x509_certificate(cert_pem.encode("utf-8")).public_key()
    except Exception as exc:
        raise AuthError(f"invalid signing certificate for kid {kid!r}: {exc}") from exc

    issuer = f"https://securetoken.google.com/{resolved_project_id}"
    try:
        claims = jwt.decode(
            token,
            key=public_key,
            algorithms=list(ALLOWED_ALGORITHMS),  # hardcoded, never caller/token-derived
            audience=resolved_project_id,
            issuer=issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "auth_time"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"token verification failed: {exc}") from exc

    now = time.time()
    _require_past_timestamp(claims, "iat", now)
    _require_past_timestamp(claims, "auth_time", now)

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("token 'sub' claim is missing or empty")
    return sub


def _require_past_timestamp(claims: Mapping[str, object], field: str, now: float) -> None:
    """Raise :class:`AuthError` unless ``claims[field]`` is numeric and at or
    before ``now`` (within :data:`CLOCK_SKEW_LEEWAY_SECONDS`)."""
    value = claims.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthError(f"token {field!r} claim is missing or not numeric")
    if value > now + CLOCK_SKEW_LEEWAY_SECONDS:
        raise AuthError(f"token {field!r} claim is in the future")


def uid_from_authorization(
    header: str | None,
    *,
    project_id: str | None = None,
    key_source: Callable[[], Mapping[str, str]] | None = None,
) -> str | None:
    """Verified ``uid`` from an ``Authorization`` header value, or ``None``.

    A missing or empty header is the clean GUEST case and returns ``None`` —
    that is not an error. A header that IS present must be a well-formed
    ``Bearer <token>``; anything else (wrong scheme, no token after the
    scheme, or a present-but-invalid/expired token) raises :class:`AuthError`
    so callers turn it into a 401 rather than silently downgrading a bad
    credential to "anonymous".

    Args:
        header: The raw ``Authorization`` header value, or ``None``.
        project_id: See :func:`verify_firebase_id_token`.
        key_source: See :func:`verify_firebase_id_token`.

    Returns:
        The verified ``uid``, or ``None`` when ``header`` is absent/empty.

    Raises:
        AuthError: ``header`` is present but not a well-formed bearer token,
            or the token fails verification.
    """
    if not header:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise AuthError("Authorization header must be 'Bearer <token>'")
    return verify_firebase_id_token(parts[1], project_id=project_id, key_source=key_source)
