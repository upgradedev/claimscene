"""End-to-end tests for optional per-user multitenancy.

Every token here is signed with a LOCALLY generated RSA keypair — no network,
no real Firebase project — mirroring the offline ``key_source`` injection seam
``tests/unit/test_auth.py`` uses for :mod:`claimscene.auth` directly, one
layer up through the real FastAPI app: ``claimscene.auth._default_key_source``
is monkeypatched so ``GET /me/library`` etc. verify these tokens with zero
network calls, exactly as CI runs it.

These tests drive the SAME shared ``client``/``api._storage`` every other
``tests/e2e/test_*.py`` file uses (see those files' own docstrings) —
isolation between tenants therefore has to hold even though every test in
this file (and every other e2e test) shares one process-wide storage object.
That is the point: a tenant's case lives under ``tenants/<uid>/...`` in that
SAME store, and this file proves a caller can never read/list/delete outside
their own prefix. Case ids and uids below are distinctive (``mt-...``) so
they never collide with another test file's guest cases sharing the same
storage.

``tests/security/test_multitenancy_isolation.py`` covers the same feature
from a pure adversarial angle (forged tokens across every wired route,
spoofing attempts) as bonus assurance in the separate pen-test CI job; the
functional/wiring coverage that counts toward the coverage gate lives here.

Ported and adapted from Cinemory's equivalent suite
(``tests/integration/test_api_multitenancy.py`` there) for ClaimScene's
case-based routes. One Cinemory test is intentionally NOT ported: "two
tenants use the identical name independently" — ClaimScene always appends a
random UUID suffix to every case id (see ``api._prepare_render``), so two
renders under the same requested ``case_id`` already produce distinct
storage keys regardless of tenant, and porting that test here would not
actually exercise per-tenant namespacing.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")  # python-multipart, needed for the form routes

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import claimscene.api as api  # noqa: E402
import claimscene.auth as auth  # noqa: E402
from claimscene.adapters import InMemoryStorage  # noqa: E402
from claimscene.jobs import TERMINAL_STATUSES  # noqa: E402
from claimscene.keys import safe_component  # noqa: E402

client = TestClient(api.app)

PROJECT_ID = "claimscene-mt-test-project"
KID = "mt-test-signing-key"


# ── local RSA keypair + Firebase-ID-token minting (offline, no network) ──────


def _generate_keypair_and_cert() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "claimscene-mt-test")])
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
def bearer_for(monkeypatch):
    """Wires ``FIREBASE_PROJECT_ID`` + an offline ``key_source`` (local RSA
    keypair, no network — see module docstring), then returns
    ``bearer_for(uid) -> {"Authorization": "Bearer <valid token for uid>"}``.
    """
    private_pem, cert_pem = _generate_keypair_and_cert()
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT_ID)
    monkeypatch.setattr(auth, "_default_key_source", lambda: {KID: cert_pem})

    def _make(uid: str, **overrides) -> dict:
        return {"Authorization": f"Bearer {_token_for(uid, private_pem, **overrides)}"}

    return _make


def _scene(scenario_id: str = "s02_left_cross") -> dict:
    return client.post("/cases/extract", data={"scenario_id": scenario_id}).json()["scene"]


def _render(case_id: str, headers: dict | None = None, *,
           scenario_id: str = "s02_left_cross", **extra) -> dict:
    scene = _scene(scenario_id)
    data = {"scene": json.dumps(scene), "case_id": case_id,
            "scenario_id": scenario_id, **extra}
    r = client.post("/cases/render", data=data, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── (a) authed happy path: create + fetch own case ───────────────────────────


def test_authed_tenant_creates_and_fetches_own_case(bearer_for):
    headers = bearer_for("mt-tenant-solo")
    body = _render("mt-solo", headers=headers)
    case_id = body["case_id"]
    assert case_id.startswith("mt-solo-")

    fetched = client.get(body["manifest_url"], headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["case_id"] == case_id

    # The underlying keys are really namespaced under this tenant's prefix.
    prefix = "tenants/" + safe_component("mt-tenant-solo") + "/"
    assert any(row["key"].startswith(prefix + case_id + "/")
              for row in api._storage.index), "case not stored under the tenant prefix"


def test_authed_tenant_render_and_verify_round_trip(bearer_for):
    headers = bearer_for("mt-tenant-verify")
    body = _render("mt-verify", headers=headers, scenario_id="s01_rear_end")
    case_id = body["case_id"]

    receipt = client.get(f"/cases/{case_id}/verify", headers=headers).json()
    assert receipt["success"] is True

    schematic = client.get(f"/cases/{case_id}/schematic", headers=headers)
    assert schematic.status_code == 200

    illustration = client.get(f"/cases/{case_id}/illustration", headers=headers)
    assert illustration.status_code == 200

    stored_receipt = client.get(f"/cases/{case_id}/receipt", headers=headers)
    assert stored_receipt.status_code == 200


# ── (b)/(c) cross-tenant + guest isolation: THE flagship proof ───────────────


def test_cross_tenant_and_guest_cannot_see_each_others_case(bearer_for):
    a = bearer_for("mt-tenant-a1")
    b = bearer_for("mt-tenant-b1")

    body = _render("mt-foo", headers=a)
    case_id = body["case_id"]

    # Tenant A sees it.
    assert client.get(f"/cases/{case_id}", headers=a).status_code == 200
    # Tenant B cannot — physically absent from B's tenant-scoped index.
    assert client.get(f"/cases/{case_id}", headers=b).status_code == 404
    # Guest cannot either — mt-foo's key lives under tenants/<a>/..., which a
    # guest's unprefixed scan never matches.
    assert client.get(f"/cases/{case_id}").status_code == 404
    # Same for every other case-scoped route.
    assert client.get(f"/cases/{case_id}/schematic", headers=b).status_code == 404
    assert client.get(f"/cases/{case_id}/schematic").status_code == 404
    assert client.get(f"/cases/{case_id}/illustration", headers=b).status_code == 404
    assert client.get(f"/cases/{case_id}/verify", headers=b).status_code == 404
    assert client.get(f"/cases/{case_id}/receipt", headers=b).status_code == 404
    # B's own library is empty — A's case is not merely hidden from GET, it
    # never appears in any enumeration B can perform either.
    assert client.get("/me/library", headers=b).json() == {"cases": []}


# ── (d)/(e) GET /me/library ───────────────────────────────────────────────────


def test_me_library_lists_only_the_authed_tenants_own_cases(bearer_for):
    a = bearer_for("mt-tenant-lib-a")
    b = bearer_for("mt-tenant-lib-b")
    r1 = _render("mt-lib-1", headers=a)
    r2 = _render("mt-lib-2", headers=a)
    r3 = _render("mt-lib-b-case", headers=b)

    lib_a = client.get("/me/library", headers=a).json()
    ids_a = {c["case_id"] for c in lib_a["cases"]}
    assert {r1["case_id"], r2["case_id"]} <= ids_a
    assert r3["case_id"] not in ids_a

    lib_b = client.get("/me/library", headers=b).json()
    ids_b = {c["case_id"] for c in lib_b["cases"]}
    assert r3["case_id"] in ids_b
    assert r1["case_id"] not in ids_b and r2["case_id"] not in ids_b

    # Shape: manifest_hash + created_at surfaced when available.
    row = next(c for c in lib_a["cases"] if c["case_id"] == r1["case_id"])
    assert row["manifest_hash"] == r1["manifest_hash"]
    assert row["created_at"]


def test_me_library_degrades_gracefully_on_an_unreadable_manifest(bearer_for):
    """Mirrors ``verify_case``'s honest-degrade contract: a manifest object
    that is indexed but can't be read/parsed still lists the case
    (best-effort, id + null hash/timestamp) rather than 500-ing the whole
    library over one bad object."""
    a = bearer_for("mt-tenant-lib-broken")
    body = _render("mt-lib-broken", headers=a)
    case_id = body["case_id"]
    prefix = api._tenant_prefix("mt-tenant-lib-broken") + f"{case_id}/"
    man_key = next(k for k in api._storage._objects
                  if k.startswith(prefix) and k.endswith("/manifest.json"))
    del api._storage._objects[man_key]

    r = client.get("/me/library", headers=a)
    assert r.status_code == 200
    row = next(x for x in r.json()["cases"] if x["case_id"] == case_id)
    assert row["manifest_hash"] is None
    assert row["created_at"] is None


def test_me_library_guest_is_401():
    r = client.get("/me/library")
    assert r.status_code == 401


def test_me_library_empty_for_a_fresh_tenant(bearer_for):
    r = client.get("/me/library", headers=bearer_for("mt-tenant-never-used"))
    assert r.status_code == 200
    assert r.json() == {"cases": []}


# ── (f)/(g) DELETE /me/data — structural scope proof ──────────────────────────


def test_me_data_delete_scope_proof(bearer_for):
    """The flagship DELETE proof: tenant A's own case is gone; a case owned by
    tenant B AND a guest case both still resolve afterward."""
    a = bearer_for("mt-tenant-del-a")
    b = bearer_for("mt-tenant-del-b")

    mine = _render("mt-del-mine", headers=a)
    other = _render("mt-del-other-tenant", headers=b)
    guest = _render("mt-del-guest-case")  # no header -> guest

    # Sanity: all three exist before the delete.
    assert client.get(mine["manifest_url"], headers=a).status_code == 200
    assert client.get(other["manifest_url"], headers=b).status_code == 200
    assert client.get(guest["manifest_url"]).status_code == 200

    resp = client.delete("/me/data", headers=a)
    assert resp.status_code == 200
    assert resp.json()["deleted"] >= 1

    # Tenant A's own case is gone.
    assert client.get(mine["manifest_url"], headers=a).status_code == 404
    assert client.get("/me/library", headers=a).json() == {"cases": []}
    # Tenant B's case and the guest case are UNTOUCHED — the delete could
    # never construct a key outside tenants/mt-tenant-del-a/.
    assert client.get(other["manifest_url"], headers=b).status_code == 200
    assert client.get(guest["manifest_url"]).status_code == 200


def test_me_data_delete_guest_is_401():
    r = client.delete("/me/data")
    assert r.status_code == 401


def test_me_data_delete_is_idempotent_on_an_empty_tenant(bearer_for):
    headers = bearer_for("mt-tenant-del-empty")
    r = client.delete("/me/data", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"deleted": 0}
    # A second call is still a clean 200/0 — never errors on "nothing to delete".
    r2 = client.delete("/me/data", headers=headers)
    assert r2.status_code == 200
    assert r2.json() == {"deleted": 0}


# ── async job submit + poll, tenant-scoped ────────────────────────────────────


def _poll_until_terminal(headers: dict | None, job_id: str, *, timeout: float = 30.0) -> dict:
    """Poll GET /cases/render/jobs/{job_id} until it reaches a terminal status.

    ClaimScene's default schematic renderer shells out to a real ``ffmpeg``
    subprocess whenever ffmpeg is on PATH (see ``tests/e2e/test_render_jobs.py``
    for the same note) — offline still means "no credentials", not "no
    subprocess" — so the bound here tolerates real (if small) wall-clock work
    rather than assuming near-instant, pure-in-memory generation.
    """
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/cases/render/jobs/{job_id}", headers=headers)
        if r.status_code == 200:
            body = r.json()
            if body.get("status") in TERMINAL_STATUSES:
                return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout}s")


def test_tenant_job_submit_and_poll_end_to_end(bearer_for):
    headers = bearer_for("mt-tenant-job")
    scene = _scene()
    r = client.post("/cases/render/jobs",
                    data={"scene": json.dumps(scene), "case_id": "mt-job-case",
                          "scenario_id": "s02_left_cross"}, headers=headers)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = _poll_until_terminal(headers, job_id)
    assert status["status"] == "done"
    result = status["result"]
    assert result["case_id"].startswith("mt-job-case-")

    # The case really landed under this tenant's storage.
    assert client.get(result["manifest_url"], headers=headers).status_code == 200


def test_other_tenant_and_guest_cannot_poll_a_known_job_id(bearer_for):
    """Even knowing the (unguessable, but suppose leaked) job id, polling it
    under a DIFFERENT verified tenant resolves to nothing — the job status
    object lives under the submitting tenant's own prefix."""
    owner = bearer_for("mt-tenant-job-owner")
    other = bearer_for("mt-tenant-job-other")
    scene = _scene()
    r = client.post("/cases/render/jobs",
                    data={"scene": json.dumps(scene), "case_id": "mt-job-owned",
                          "scenario_id": "s02_left_cross"}, headers=owner)
    job_id = r.json()["job_id"]
    _poll_until_terminal(owner, job_id)

    assert client.get(f"/cases/render/jobs/{job_id}", headers=other).status_code == 404
    assert client.get(f"/cases/render/jobs/{job_id}").status_code == 404  # guest


# ── FIREBASE_PROJECT_ID unset: proves the additive default ───────────────────


def test_project_id_unset_treats_a_bearer_header_as_guest(bearer_for, monkeypatch):
    """The critical additive-default proof: even a WELL-FORMED, validly-signed
    bearer token is ignored (not 401'd, not honoured) when this deployment has
    no FIREBASE_PROJECT_ID configured — multitenancy is off, full stop."""
    headers = bearer_for("mt-tenant-should-be-ignored")
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)

    body = _render("mt-project-off", headers=headers)
    case_id = body["case_id"]

    # Stored as a GUEST case: fetchable with NO header at all.
    fetched = client.get(f"/cases/{case_id}")
    assert fetched.status_code == 200
    assert fetched.json()["case_id"] == case_id

    # And it is NOT namespaced under any tenant prefix.
    assert not any(row["key"].startswith("tenants/") and case_id in row["key"]
                  for row in api._storage.index)


def test_guest_storage_is_the_identical_module_object_with_multitenancy_configured(
    bearer_for,
):
    """The byte-identity proof for the OTHER guest configuration: every other
    test in this module gets its "guest is unaffected" evidence from
    ``FIREBASE_PROJECT_ID`` being unset entirely (CI's default — see
    ``test_project_id_unset_treats_a_bearer_header_as_guest`` above), which
    only exercises ``get_tenant``'s short-circuit and never reaches
    ``_tenant_storage(None)``/``_tenant_job_storage(None)`` at all.

    ``bearer_for`` sets ``FIREBASE_PROJECT_ID`` (the production configuration
    once multitenancy is actually turned on); calling the resolvers directly
    with ``None`` (the header-absent / guest case on THAT deployment) proves
    they return the EXACT pre-existing module-level objects — ``is``, not
    ``==`` — not merely an equivalent-looking one.
    """
    assert api._tenant_storage(None) is api._storage
    assert api._tenant_job_storage(None) is api._job_storage()


# ── spoofing resistance: only the verified token can set the tenant ─────────


def test_tenant_cannot_be_spoofed_via_query_param_header_or_body(bearer_for):
    a = bearer_for("mt-tenant-spoof-victim")
    body = _render("mt-spoof-target", headers=a)
    case_id = body["case_id"]

    # An UNAUTHENTICATED caller tries every non-token channel to claim tenant A.
    attempts = [
        client.get(f"/cases/{case_id}?tenant=mt-tenant-spoof-victim"),
        client.get(f"/cases/{case_id}", headers={"X-Tenant": "mt-tenant-spoof-victim"}),
        client.get("/me/library?tenant=mt-tenant-spoof-victim"),
        client.get("/me/library", headers={"X-Tenant": "mt-tenant-spoof-victim"}),
    ]
    # Every one of these still resolves as GUEST: the case lookup 404s (guest
    # cannot see a tenant-scoped case) and /me/library still demands real auth.
    assert attempts[0].status_code == 404
    assert attempts[1].status_code == 404
    assert attempts[2].status_code == 401
    assert attempts[3].status_code == 401

    # A body field named "tenant" on a render is never bound to any parameter
    # (FastAPI's Form() routes only read declared fields) — the case is still
    # created as GUEST, not attributed to the claimed tenant.
    r = client.post("/cases/render", data={
        "scene": json.dumps(_scene()), "case_id": "mt-spoof-body",
        "tenant": "mt-tenant-spoof-victim",
    })
    assert r.status_code == 200
    spoofed_id = r.json()["case_id"]
    assert not any(row["key"].startswith("tenants/") and spoofed_id in row["key"]
                  for row in api._storage.index)


def test_authed_caller_cannot_override_their_own_tenant_via_spoofing(bearer_for):
    """A DIFFERENT, more dangerous shape of the same attack: tenant B is
    authenticated (valid token) but tries to reach into tenant A's data via a
    query param / header. The verified token wins every time."""
    a = bearer_for("mt-tenant-spoof-a")
    b = bearer_for("mt-tenant-spoof-b")
    body = _render("mt-spoof-a-case", headers=a)
    case_id = body["case_id"]

    r1 = client.get(f"/cases/{case_id}?tenant=mt-tenant-spoof-a", headers=b)
    r2 = client.get(f"/cases/{case_id}", headers={**b, "X-Tenant": "mt-tenant-spoof-a"})
    assert r1.status_code == 404  # still resolves as tenant B, not A
    assert r2.status_code == 404

    lib = client.get("/me/library?tenant=mt-tenant-spoof-a", headers=b).json()
    assert lib["cases"] == []  # B's own (empty) library, never A's


# ── forged/invalid credentials -> 401, never a silent guest downgrade ────────


def test_garbage_bearer_token_is_401_not_guest():
    r = client.get("/me/library", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401


def test_expired_token_is_401(bearer_for):
    headers = bearer_for("mt-tenant-expired",
                         iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    r = client.get("/me/library", headers=headers)
    assert r.status_code == 401


def test_token_signed_by_a_different_key_is_401(bearer_for):
    """The classic forged-token shape: the attacker's own (unrelated) private
    key signs a token that CLAIMS the legitimate ``kid``. ``key_source``
    resolves the real public cert for that kid, so the signature check fails
    even though the kid lookup itself succeeds — ``bearer_for`` wires
    ``FIREBASE_PROJECT_ID`` + the real key_source seam this depends on."""
    attacker_private, _attacker_cert = _generate_keypair_and_cert()
    forged = _token_for("mt-tenant-forger", attacker_private)
    assert jwt.get_unverified_header(forged)["kid"] == KID  # claims the real kid
    r = client.get("/me/library", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


# ── direct construction: the isolation-core wrapper's own contract ──────────


def test_tenant_scoped_storage_prefixes_every_operation():
    base = InMemoryStorage()
    wrapper = api._TenantScopedStorage(base, "tenants/mt-direct/")
    url = wrapper.put("a/b.txt", b"payload", content_type="text/plain")
    assert base.get("tenants/mt-direct/a/b.txt") == b"payload"
    assert wrapper.get("a/b.txt") == b"payload"
    assert wrapper.exists("a/b.txt") is True
    assert base.exists("a/b.txt") is False  # unprefixed key never touched
    assert "tenants/mt-direct/a/b.txt" in url


def test_tenant_scoped_storage_index_is_filtered_and_stripped():
    base = InMemoryStorage()
    base.put("tenants/mt-x/foo/illustration/ab/abcd/illustration.mp4", b"1")
    base.put("tenants/mt-y/bar/illustration/cd/cdef/illustration.mp4", b"2")
    base.put("guest-case/illustration/ef/efgh/illustration.mp4", b"3")

    wrapper = api._TenantScopedStorage(base, "tenants/mt-x/")
    assert wrapper.index == [
        {"key": "foo/illustration/ab/abcd/illustration.mp4", "size": 1,
         "content_type": "application/octet-stream"}
    ]


def test_tenant_scoped_storage_get_url_exposed_only_when_base_has_it():
    class _NoGetUrl:
        def put(self, key, data, *, content_type="application/octet-stream"):
            return key

        def get(self, key):
            return b""

        def exists(self, key):
            return False

    assert not hasattr(api._TenantScopedStorage(_NoGetUrl(), "tenants/x/"), "get_url")

    class _WithGetUrl(_NoGetUrl):
        def get_url(self, key, *, expires_in=3600):
            return f"https://example.test/{key}?exp={expires_in}"

    wrapper = api._TenantScopedStorage(_WithGetUrl(), "tenants/mt-z/")
    assert hasattr(wrapper, "get_url")
    assert wrapper.get_url("v.mp4") == "https://example.test/tenants/mt-z/v.mp4?exp=3600"


def test_tenant_scoped_storage_delete_exposed_only_when_base_has_it():
    class _NoDelete:
        def put(self, key, data, *, content_type="application/octet-stream"):
            return key

        def get(self, key):
            return b""

        def exists(self, key):
            return False

    assert not hasattr(api._TenantScopedStorage(_NoDelete(), "tenants/x/"), "delete")

    # InMemoryStorage has delete (added for multitenancy) -> exposed and prefixed.
    base = InMemoryStorage()
    base.put("tenants/mt-w/r/illustration/aa/bb/illustration.mp4", b"x")
    wrapper = api._TenantScopedStorage(base, "tenants/mt-w/")
    wrapper.delete("r/illustration/aa/bb/illustration.mp4")
    assert base.exists("tenants/mt-w/r/illustration/aa/bb/illustration.mp4") is False


def test_tenant_scoped_storage_reload_index_is_always_safe():
    class _NoReload:
        def __init__(self) -> None:
            self.index: list[dict] = []

        def put(self, key, data, *, content_type="application/octet-stream"):
            return key

        def get(self, key):
            return b""

        def exists(self, key):
            return False

    base = _NoReload()  # no reload_index of its own
    assert not hasattr(base, "reload_index")
    wrapper = api._TenantScopedStorage(base, "tenants/mt-v/")
    assert wrapper.reload_index() == []  # no-op, never raises


def test_tenant_scoped_storage_reload_index_delegates_when_base_has_one():
    """The B2Storage-shaped case: when the base DOES expose ``reload_index``
    (query-time freshness against the durable bucket index), the wrapper
    really calls it, not just skips it — mirrors the no-``reload_index`` case
    above (which proves the absent-case no-op) from the other side."""
    calls = []

    class _WithReload:
        def __init__(self) -> None:
            self.index: list[dict] = []

        def reload_index(self) -> list[dict]:
            calls.append(1)
            self.index = [{"key": "tenants/mt-r/reloaded/illustration/a/b/illustration.mp4",
                          "size": 1, "content_type": "video/mp4"}]
            return self.index

    wrapper = api._TenantScopedStorage(_WithReload(), "tenants/mt-r/")
    result = wrapper.reload_index()
    assert calls == [1]
    assert result == [{"key": "reloaded/illustration/a/b/illustration.mp4",
                       "size": 1, "content_type": "video/mp4"}]


def test_tenant_prefix_sanitises_a_hostile_uid():
    """Defence in depth: even though a Firebase uid is never attacker-chosen,
    ``_tenant_prefix`` still runs it through ``safe_component`` — a uid that
    somehow contained a path separator could never escape ``tenants/``."""
    assert api._tenant_prefix("normal-uid-123") == "tenants/normal-uid-123/"
    assert api._tenant_prefix("../../etc/passwd") == "tenants/passwd/"


# ── get_url present branch: authed tenant playback 302s with the prefixed key ─


class _PresigningStorage:
    """Minimal live-shaped backend: durable index + a ``get_url`` presigner —
    mirrors ``tests/integration/test_b2_storage.py``'s guest-path double, here
    used to exercise the AUTHED branch of ``_TenantScopedStorage``."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.index: list[dict] = []
        self.signed: list[str] = []

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream"):
        self._objects[key] = data
        self.index.append({"key": key, "size": len(data), "content_type": content_type})
        return f"https://bucket.example/{key}"

    def get(self, key: str) -> bytes:
        return self._objects[key]

    def exists(self, key: str) -> bool:
        return key in self._objects

    def get_url(self, key: str, *, expires_in: int = 3600) -> str:
        self.signed.append(key)
        return f"https://bucket.example/{key}?X-Amz-Expires={expires_in}&sig={len(self.signed)}"


def test_authed_tenant_illustration_redirects_to_a_presigned_url_under_the_tenant_prefix(
    bearer_for, monkeypatch
):
    storage = _PresigningStorage()
    monkeypatch.setattr(api, "_storage", storage)
    headers = bearer_for("mt-tenant-presign")
    prefix = api._tenant_prefix("mt-tenant-presign")
    key = "livecase/illustration/ab/" + "a" * 64 + "/illustration.mp4"
    storage.put(f"{prefix}{key}", b"real-video-bytes", content_type="video/mp4")

    r = client.get("/cases/livecase/illustration", headers=headers, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    # The PREFIXED key was signed — never the bare tenant-relative key.
    assert location.startswith(f"https://bucket.example/{prefix}{key}?")
    assert storage.signed == [f"{prefix}{key}"]

    # A guest (or a different tenant) still gets a clean 404, not a signed
    # guess at someone else's object.
    assert client.get("/cases/livecase/illustration").status_code == 404
