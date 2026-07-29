"""Adversarial, fully-offline tests for :mod:`claimscene.auth`.

Every token here is signed with a locally generated RSA keypair — no network,
no real Firebase project, no Google connectivity. The ``key_source``
injection point is what makes this possible: production wires the live
Google cert fetch (``claimscene.auth._default_key_source``); tests wire a fake
exposing a self-signed x509 cert built for exactly this purpose, the same
PEM-x509-per-``kid`` shape Google's own endpoint returns.

Ported from Cinemory's adversarial suite for the identical module (this repo
shares that auth primitive verbatim — see ``src/claimscene/auth.py``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from claimscene import auth
from claimscene.auth import AuthError, uid_from_authorization, verify_firebase_id_token

PROJECT_ID = "claimscene-test-project"
KID = "test-signing-key-1"


# ── Local RSA keypair + self-signed cert fixtures ───────────────────────────
# Google's certs endpoint returns a PEM x509 certificate per kid (not a raw
# JWK) — these fixtures build the same shape locally so the suite never
# touches the network.


def _generate_keypair_and_cert() -> tuple[str, str]:
    """Return ``(private_key_pem, public_cert_pem)`` for a fresh 2048-bit RSA
    keypair wrapped in a self-signed x509 cert."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "claimscene-test")])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return private_pem, cert_pem


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    """The "legitimate" signing key — its cert is what ``key_source`` exposes
    under :data:`KID`."""
    return _generate_keypair_and_cert()


@pytest.fixture(scope="module")
def attacker_keypair() -> tuple[str, str]:
    """A second, unrelated keypair whose cert is never exposed via
    ``key_source`` under any kid. Proves a signature made with the WRONG
    private key is rejected even when the token claims the legitimate kid."""
    return _generate_keypair_and_cert()


@pytest.fixture
def key_source(keypair):
    _private_pem, cert_pem = keypair

    def _source():
        return {KID: cert_pem}

    return _source


# ── Token construction helpers ──────────────────────────────────────────────


def _valid_claims(*, now: float | None = None, **overrides) -> dict:
    now = time.time() if now is None else now
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT_ID}",
        "aud": PROJECT_ID,
        "sub": "firebase-uid-abc123",
        "iat": int(now) - 30,
        "exp": int(now) + 3600,
        "auth_time": int(now) - 30,
    }
    claims.update(overrides)
    return claims


def _sign(private_key_pem: str, claims: dict, *, kid: str = KID) -> str:
    return jwt.encode(claims, private_key_pem, algorithm="RS256", headers={"kid": kid})


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _unverified_segments(header: dict, payload: dict) -> tuple[str, str]:
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return h, p


def _tamper_payload(token: str, **overrides) -> str:
    """Re-encode the payload segment with ``overrides`` applied, WITHOUT
    re-signing — the original signature no longer matches the new payload."""
    h, p, s = token.split(".")
    payload = json.loads(_b64url_decode(p))
    payload.update(overrides)
    new_p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{h}.{new_p}.{s}"


# ── (a) Happy path ───────────────────────────────────────────────────────


def test_valid_token_returns_uid(keypair, key_source):
    private_pem, _cert_pem = keypair
    token = _sign(private_pem, _valid_claims())
    uid = verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)
    assert uid == "firebase-uid-abc123"


# ── (b) Time-based claims: exp / iat / auth_time ─────────────────────────


def test_expired_token_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(
        private_pem,
        _valid_claims(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600),
    )
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_future_iat_rejected(keypair, key_source):
    private_pem, _ = keypair
    far_future = int(time.time()) + 10_000
    token = _sign(private_pem, _valid_claims(iat=far_future, auth_time=far_future))
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_future_auth_time_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims(auth_time=int(time.time()) + 10_000))
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_missing_auth_time_rejected(keypair, key_source):
    private_pem, _ = keypair
    claims = _valid_claims()
    del claims["auth_time"]
    token = _sign(private_pem, claims)
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_non_numeric_auth_time_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims(auth_time="not-a-number"))
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_clock_skew_within_leeway_accepted(keypair, key_source):
    """A few seconds of future iat/auth_time (ordinary clock skew) is
    tolerated, not rejected outright."""
    private_pem, _ = keypair
    now = time.time()
    token = _sign(private_pem, _valid_claims(iat=int(now) + 5, auth_time=int(now) + 5))
    uid = verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)
    assert uid == "firebase-uid-abc123"


# ── (c) Audience / issuer ────────────────────────────────────────────────


def test_wrong_audience_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims(aud="some-other-project"))
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_wrong_issuer_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(
        private_pem,
        _valid_claims(iss="https://securetoken.google.com/some-other-project"),
    )
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_missing_audience_rejected(keypair, key_source):
    private_pem, _ = keypair
    claims = _valid_claims()
    del claims["aud"]
    token = _sign(private_pem, claims)
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


# ── (d) Signature integrity ──────────────────────────────────────────────


def test_tampered_payload_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims())
    tampered = _tamper_payload(token, sub="someone-elses-uid")
    with pytest.raises(AuthError):
        verify_firebase_id_token(tampered, project_id=PROJECT_ID, key_source=key_source)


def test_signed_by_different_key_rejected(attacker_keypair, key_source):
    """Signed with an attacker-controlled private key, but the token claims
    the LEGITIMATE kid: key_source resolves the real public cert for that
    kid, so the signature check fails even though the kid lookup succeeds."""
    attacker_private_pem, _attacker_cert_pem = attacker_keypair
    token = _sign(attacker_private_pem, _valid_claims(), kid=KID)
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


# ── (e) Algorithm safety: alg:none and RS256/HS256 confusion ────────────
#
# Both hand-craft the token bytes directly instead of going through
# jwt.encode(): PyJWT (>=2.4) itself refuses to encode an HS256 token using
# an asymmetric key as the HMAC secret (raises InvalidKeyError), so the
# attack has to be built at the wire level to even exist as a token — exactly
# what a real attacker would do. Everything else about these tokens (a `kid`
# key_source actually knows about, otherwise-valid claims) is kept valid, so
# the ONLY thing that can trigger rejection is the algorithm gate itself, not
# an unrelated malformed-token/unknown-kid path.


def test_alg_none_rejected(key_source):
    header, payload = _unverified_segments(
        {"alg": "none", "typ": "JWT", "kid": KID}, _valid_claims()
    )
    token = f"{header}.{payload}."  # empty signature segment — the classic alg:none shape
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_hs256_confusion_with_public_key_as_secret_rejected(keypair, key_source):
    """The textbook RS256->HS256 downgrade: the attacker knows the RSA
    PUBLIC cert (it's public by definition) and HMAC-signs a forged token
    using its PEM bytes as the HMAC "secret". A verifier that let the
    token's own `alg` header pick the algorithm, or that allowed HS256
    alongside RS256, would wrongly accept this. Ours rejects it before even
    reading the signature bytes, because `alg` is checked against a
    hardcoded allow-list first."""
    _private_pem, cert_pem = keypair
    header, payload = _unverified_segments(
        {"alg": "HS256", "typ": "JWT", "kid": KID}, _valid_claims()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    forged_sig = hmac.new(cert_pem.encode("utf-8"), signing_input, hashlib.sha256).digest()
    token = f"{header}.{payload}.{_b64url(forged_sig)}"
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_unsupported_algorithm_rejected(key_source):
    header, payload = _unverified_segments(
        {"alg": "ES256", "typ": "JWT", "kid": KID}, _valid_claims()
    )
    token = f"{header}.{payload}.{_b64url(b'not-a-real-signature')}"
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


# ── (f) sub / kid ─────────────────────────────────────────────────────────


def test_missing_sub_rejected(keypair, key_source):
    private_pem, _ = keypair
    claims = _valid_claims()
    del claims["sub"]
    token = _sign(private_pem, claims)
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_empty_sub_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims(sub=""))
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_missing_kid_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = jwt.encode(_valid_claims(), private_pem, algorithm="RS256")  # no `kid` header
    assert "kid" not in jwt.get_unverified_header(token)
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


def test_unknown_kid_rejected(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims(), kid="a-kid-key-source-has-never-heard-of")
    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)


# ── (g) Malformed input / misconfiguration ────────────────────────────────


def test_garbage_token_rejected(key_source):
    with pytest.raises(AuthError):
        verify_firebase_id_token("not-a-jwt-at-all", project_id=PROJECT_ID, key_source=key_source)


def test_empty_token_rejected(key_source):
    with pytest.raises(AuthError):
        verify_firebase_id_token("", project_id=PROJECT_ID, key_source=key_source)


def test_missing_project_id_raises(monkeypatch, key_source):
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    with pytest.raises(AuthError):
        verify_firebase_id_token("irrelevant", project_id=None, key_source=key_source)


def test_project_id_defaults_from_env(monkeypatch, keypair, key_source):
    private_pem, _ = keypair
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT_ID)
    token = _sign(private_pem, _valid_claims())
    uid = verify_firebase_id_token(token, key_source=key_source)  # no project_id kwarg
    assert uid == "firebase-uid-abc123"


def test_explicit_project_id_overrides_env(monkeypatch, keypair, key_source):
    private_pem, _ = keypair
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "a-totally-different-project")
    token = _sign(private_pem, _valid_claims())  # issued for PROJECT_ID, not the env value
    uid = verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=key_source)
    assert uid == "firebase-uid-abc123"


def test_key_source_failure_becomes_autherror(keypair):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims())

    def _broken_source():
        raise RuntimeError("network is down")

    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=_broken_source)


def test_invalid_certificate_becomes_autherror(keypair):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims())

    def _bad_cert_source():
        return {KID: "not a real PEM certificate"}

    with pytest.raises(AuthError):
        verify_firebase_id_token(token, project_id=PROJECT_ID, key_source=_bad_cert_source)


# ── uid_from_authorization ────────────────────────────────────────────────


def test_uid_from_authorization_no_header_is_guest(key_source):
    assert uid_from_authorization(None, project_id=PROJECT_ID, key_source=key_source) is None


def test_uid_from_authorization_empty_header_is_guest(key_source):
    assert uid_from_authorization("", project_id=PROJECT_ID, key_source=key_source) is None


def test_uid_from_authorization_valid_bearer_returns_uid(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims())
    uid = uid_from_authorization(f"Bearer {token}", project_id=PROJECT_ID, key_source=key_source)
    assert uid == "firebase-uid-abc123"


def test_uid_from_authorization_scheme_is_case_insensitive(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(private_pem, _valid_claims())
    uid = uid_from_authorization(f"bearer {token}", project_id=PROJECT_ID, key_source=key_source)
    assert uid == "firebase-uid-abc123"


@pytest.mark.parametrize(
    "header",
    [
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer ",
        "BearerNoSpaceToken",
        "Bearer token extra-garbage",
        "   ",
    ],
)
def test_uid_from_authorization_malformed_header_raises(header, key_source):
    with pytest.raises(AuthError):
        uid_from_authorization(header, project_id=PROJECT_ID, key_source=key_source)


def test_uid_from_authorization_invalid_token_raises(key_source):
    with pytest.raises(AuthError):
        uid_from_authorization(
            "Bearer not-a-valid-jwt", project_id=PROJECT_ID, key_source=key_source
        )


def test_uid_from_authorization_expired_token_raises(keypair, key_source):
    private_pem, _ = keypair
    token = _sign(
        private_pem,
        _valid_claims(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600),
    )
    with pytest.raises(AuthError):
        uid_from_authorization(f"Bearer {token}", project_id=PROJECT_ID, key_source=key_source)


# ── Pure helpers: Cache-Control parsing + default key_source caching ──────


@pytest.mark.parametrize(
    ("header_value", "expected"),
    [
        ("public, max-age=3600", 3600.0),
        ("max-age=0", 0.0),
        ("no-cache", None),
        (None, None),
        ("", None),
        ("public, max-age=not-a-number", None),
        ("max-age=-5", None),
        ("private, max-age=120, must-revalidate", 120.0),
    ],
)
def test_parse_cache_max_age(header_value, expected):
    assert auth._parse_cache_max_age(header_value) == expected


def test_default_key_source_cache_hit_returns_cached_value_with_no_network(monkeypatch):
    sentinel = {"cached-kid": "cached-cert-pem"}
    monkeypatch.setitem(auth._certs_cache, "certs", sentinel)
    monkeypatch.setitem(auth._certs_cache, "expires_at", time.time() + 3600)

    def _fail_if_called(_now):
        raise AssertionError("must not hit the network on a cache hit")

    monkeypatch.setattr(auth, "_refresh_certs_cache", _fail_if_called)

    assert auth._default_key_source() is sentinel


def test_default_key_source_cache_miss_delegates_to_refresh(monkeypatch):
    monkeypatch.setitem(auth._certs_cache, "certs", None)
    monkeypatch.setitem(auth._certs_cache, "expires_at", 0.0)

    sentinel = {"fresh-kid": "fresh-cert-pem"}
    monkeypatch.setattr(auth, "_refresh_certs_cache", lambda now: sentinel)

    assert auth._default_key_source() is sentinel
