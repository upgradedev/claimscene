"""PEN-TEST — Multitenancy isolation.

Threat: a signed-in caller's cases must be isolated to their own tenant. This
file adversarially proves the isolation boundary from the outside, driving
the real FastAPI app exactly as CI runs it (offline, InMemoryStorage, no
network — see ``tests/security/conftest.py``):

  * a forged/garbage/expired bearer credential is REJECTED (401) on every
    route that resolves a tenant — never silently downgraded to guest;
  * cross-tenant (and guest) reads of another tenant's case 404 — the row is
    physically absent from the caller's own tenant-scoped index, not merely
    rejected by a comparison;
  * ``DELETE /me/data`` cannot be steered outside the caller's own prefix;
  * the tenant cannot be spoofed via a query param, a non-``Authorization``
    header, or a request-body field — only a verified token sets it.

Functional/wiring coverage (happy paths, ``/me/library`` shape, async job
polling, the ``get_url`` presign branch, direct ``_TenantScopedStorage``
construction tests) lives in ``tests/e2e/test_multitenancy.py`` — it counts
toward the coverage gate this file does not (``tests/security`` runs in its
own ``pen-test`` CI job). Tokens here are signed with a LOCALLY generated RSA
keypair (no network) via the same offline ``key_source`` seam
``tests/unit/test_auth.py`` uses.

Ported and adapted from Cinemory's equivalent suite
(``tests/security/test_multitenancy_isolation.py`` there).
"""
from __future__ import annotations

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

PROJECT_ID = "claimscene-sec-mt-test-project"
KID = "sec-mt-test-signing-key"

# A minimal, valid SceneGraph — mirrors tests/security/test_scene_vocabulary_gate.py's
# own fixture (each security file keeps its own copy rather than sharing one).
VALID_SCENE = {
    "schema": "claimscene/scene/v1",
    "road": {"layout": "x_intersection", "lanes_per_direction": 1, "signal": "traffic_light"},
    "vehicles": [
        {"id": "veh_a", "kind": "car", "color": "silver",
         "damage": [{"clock_position": 2, "severity": "crush"}]},
        {"id": "veh_b", "kind": "van", "color": "green"},
    ],
    "movements": [
        {"vehicle_id": "veh_a", "approach": "N", "maneuver": "left_turn", "speed_band": "low"},
        {"vehicle_id": "veh_b", "approach": "S", "maneuver": "straight", "speed_band": "moderate"},
    ],
    "impacts": [{"vehicle_id": "veh_a", "clock_position": 2}],
}


def _scene_json() -> str:
    return json.dumps(VALID_SCENE)


def _generate_keypair_and_cert() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "claimscene-sec-mt-test")])
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


def _token_for(uid: str, private_pem: str, **overrides) -> str:
    now = time.time()
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT_ID}",
        "aud": PROJECT_ID,
        "sub": uid,
        "iat": int(now) - 30,
        "exp": int(now) + 3600,
        "auth_time": int(now) - 30,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def bearer_for(client, monkeypatch):
    """Wires ``FIREBASE_PROJECT_ID`` + an offline ``key_source`` (local RSA
    keypair, no network), then returns ``bearer_for(uid) -> headers``.
    Depends on the ``client`` fixture (see ``conftest.py``) so it runs after
    that fixture's module reload.
    """
    private_pem, cert_pem = _generate_keypair_and_cert()
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT_ID)
    monkeypatch.setattr(auth, "_default_key_source", lambda: {KID: cert_pem})

    def _make(uid: str, **overrides) -> dict:
        return {"Authorization": f"Bearer {_token_for(uid, private_pem, **overrides)}"}

    return _make


def _garbage_headers() -> dict:
    return {"Authorization": "Bearer this-is-not-a-valid-jwt-at-all"}


# ── (1) forged/garbage token -> 401 on EVERY wired route, never guest ────────
# Deliberately excludes /health, /scenarios, /cases/extract and
# /cases/preview-schematic: none of those touch storage, so they carry no
# tenant dependency by design (see api.py's module docstring) — a liveness
# probe or a stateless preview must never 401 on a bad credential — see
# test_forged_token_does_not_break_unauthenticated_routes below.

_WIRED_ROUTE_CALLS = [
    ("POST /cases/render", lambda c, h: c.post(
        "/cases/render", data={"scene": _scene_json(), "case_id": "sec-401-a"}, headers=h)),
    ("POST /cases/render/jobs", lambda c, h: c.post(
        "/cases/render/jobs", data={"scene": _scene_json(), "case_id": "sec-401-b"}, headers=h)),
    ("GET /cases/render/jobs/{id}", lambda c, h: c.get(
        "/cases/render/jobs/nonexistent-job-id", headers=h)),
    ("GET /cases/{id}", lambda c, h: c.get("/cases/nonexistent-case", headers=h)),
    ("GET /cases/{id}/schematic", lambda c, h: c.get(
        "/cases/nonexistent-case/schematic", headers=h)),
    ("GET /cases/{id}/illustration", lambda c, h: c.get(
        "/cases/nonexistent-case/illustration", headers=h)),
    ("GET /cases/{id}/verify", lambda c, h: c.get(
        "/cases/nonexistent-case/verify", headers=h)),
    ("GET /cases/{id}/receipt", lambda c, h: c.get(
        "/cases/nonexistent-case/receipt", headers=h)),
    ("GET /me/library", lambda c, h: c.get("/me/library", headers=h)),
    ("DELETE /me/data", lambda c, h: c.delete("/me/data", headers=h)),
]


@pytest.mark.parametrize(
    "call", [c for _, c in _WIRED_ROUTE_CALLS], ids=[label for label, _ in _WIRED_ROUTE_CALLS]
)
def test_forged_token_is_401_on_every_wired_route(client, monkeypatch, call):
    """A bad credential is a hard 401 everywhere the tenant dependency is
    wired — proving ``get_tenant`` never lets a present-but-invalid token
    fall through as guest, on ANY of the 10 routes it guards. The route
    handler body never even runs (FastAPI resolves dependencies first), so
    an otherwise-404-worthy nonexistent id is irrelevant here.

    Requires ``FIREBASE_PROJECT_ID`` set: with it unset, ``get_tenant``
    correctly treats EVERY request as guest without even looking at the
    header (see ``test_project_id_unset_treats_a_bearer_header_as_guest`` in
    the e2e suite) — so a forged token would be silently ignored exactly
    like a valid one, and this test would be proving nothing.
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT_ID)
    r = call(client, _garbage_headers())
    assert r.status_code == 401


def test_forged_token_does_not_break_unauthenticated_routes(client):
    """/health, /scenarios, /cases/extract and /cases/preview-schematic carry
    no tenant dependency by design (none of them touch storage) — a garbage
    credential in flight must not break any of them."""
    headers = _garbage_headers()
    assert client.get("/health", headers=headers).status_code == 200
    assert client.get("/scenarios", headers=headers).status_code == 200
    assert client.post("/cases/extract", data={"scenario_id": "s01_rear_end"},
                       headers=headers).status_code == 200
    assert client.post("/cases/preview-schematic", json=VALID_SCENE,
                       headers=headers).status_code == 200


def test_expired_token_is_401_not_guest(client, bearer_for):
    headers = bearer_for("sec-mt-expired",
                         iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    assert client.get("/me/library", headers=headers).status_code == 401


def test_malformed_bearer_scheme_is_401_not_guest(client, monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT_ID)
    r = client.get("/me/library", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401


def test_token_signed_by_attacker_key_is_401(client, bearer_for):
    """The classic forged-token shape: signed with an unrelated private key
    but CLAIMING the legitimate kid. ``key_source`` resolves the real public
    cert for that kid, so the signature check fails even though the kid
    lookup succeeds."""
    attacker_private, _attacker_cert = _generate_keypair_and_cert()
    forged = _token_for("sec-mt-forger", attacker_private)
    r = client.get("/me/library", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


# ── (2) cross-tenant leak: the flagship isolation proof ──────────────────────


def test_cross_tenant_case_leak_is_impossible(client, bearer_for):
    """Tenant A renders a case. Tenant B's GET must 404 — A's row is
    physically absent from B's tenant-scoped index, not merely rejected by
    an id comparison. A GUEST's GET must 404 too — A's case lives under
    ``tenants/<A>/`` which a guest's unprefixed scan never matches."""
    a = bearer_for("sec-mt-tenant-a")
    b = bearer_for("sec-mt-tenant-b")

    r = client.post("/cases/render",
                    data={"scene": _scene_json(), "case_id": "foo"}, headers=a)
    assert r.status_code == 200
    case_id = r.json()["case_id"]

    assert client.get(f"/cases/{case_id}", headers=a).status_code == 200  # sanity: A sees it
    assert client.get(f"/cases/{case_id}", headers=b).status_code == 404
    assert client.get(f"/cases/{case_id}").status_code == 404
    assert client.get(f"/cases/{case_id}/schematic", headers=b).status_code == 404
    assert client.get(f"/cases/{case_id}/illustration", headers=b).status_code == 404
    assert client.get(f"/cases/{case_id}/verify", headers=b).status_code == 404
    assert client.get(f"/cases/{case_id}/receipt", headers=b).status_code == 404
    # B's own library is empty — A's case is not merely hidden from GET, it
    # never appears in any enumeration B can perform either.
    assert client.get("/me/library", headers=b).json() == {"cases": []}


# ── (3) DELETE /me/data cannot reach outside the caller's own prefix ────────


def test_delete_my_data_cannot_reach_outside_own_prefix(client, bearer_for):
    a = bearer_for("sec-mt-del-a")
    b = bearer_for("sec-mt-del-b")
    ra = client.post("/cases/render",
                     data={"scene": _scene_json(), "case_id": "mine"}, headers=a)
    rb = client.post("/cases/render",
                     data={"scene": _scene_json(), "case_id": "not-mine"}, headers=b)
    rg = client.post("/cases/render",
                     data={"scene": _scene_json(), "case_id": "guest-untouched"})
    assert ra.status_code == 200 and rb.status_code == 200 and rg.status_code == 200

    r = client.delete("/me/data", headers=a)
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1

    assert client.get(f"/cases/{ra.json()['case_id']}", headers=a).status_code == 404
    # Neither tenant B's case nor the guest case were reachable through this
    # call — there is no key this loop could ever construct outside
    # tenants/sec-mt-del-a/.
    assert client.get(f"/cases/{rb.json()['case_id']}", headers=b).status_code == 200
    assert client.get(f"/cases/{rg.json()['case_id']}").status_code == 200


def test_delete_my_data_guest_is_401_never_deletes_anything(client):
    r = client.delete("/me/data")
    assert r.status_code == 401


# ── (4) tenant cannot be spoofed via non-token channels ──────────────────────


def test_spoofed_tenant_via_query_header_or_body_is_ignored(client, bearer_for):
    a = bearer_for("sec-mt-spoof-a")
    b = bearer_for("sec-mt-spoof-b")
    ra = client.post("/cases/render", data={
        "scene": _scene_json(), "case_id": "spoof-target"}, headers=a)
    assert ra.status_code == 200
    case_id = ra.json()["case_id"]

    # Attacker B, authenticated as themselves, tries every non-token channel
    # to reach into A's data.
    r1 = client.get(f"/cases/{case_id}?tenant=sec-mt-spoof-a", headers=b)
    r2 = client.get(f"/cases/{case_id}", headers={**b, "X-Tenant": "sec-mt-spoof-a"})
    assert r1.status_code == 404
    assert r2.status_code == 404

    # A body field named "tenant" on a render is never bound to any
    # parameter (FastAPI's Form() routes only read declared fields) — B's
    # own case, not attributed to A.
    r3 = client.post("/cases/render", data={
        "scene": _scene_json(), "case_id": "spoof-attempt",
        "tenant": "sec-mt-spoof-a"}, headers=b)
    assert r3.status_code == 200
    spoofed_id = r3.json()["case_id"]
    assert client.get(f"/cases/{spoofed_id}", headers=a).status_code == 404
    assert client.get(f"/cases/{spoofed_id}", headers=b).status_code == 200
