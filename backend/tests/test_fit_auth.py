"""Unit tests for Forge Invocation Token verification.

Covers the verify_fit primitive in isolation. Middleware-level
integration is covered in `test_forge_middleware.py`.

We generate an RSA keypair per test session, build a stub
SigningKeyResolver that returns the matching public key as a PyJWK,
and round-trip tokens through it. No network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK

from app.forge.fit_auth import (
    EXPECTED_ISSUER,
    ForgeAuthError,
    SigningKeyResolver,
    verify_fit,
)

APP_ARI = "ari:cloud:ecosystem::app/ef458a66-330e-4df7-a84f-d856349d84e0"
INSTALL_ARI = "ari:cloud:ecosystem::installation/0a3a7799-53ae-4a5b-9e7e-03338980abb5"
CLOUD_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


@pytest.fixture
def stub_resolver(rsa_keypair: tuple[bytes, bytes]) -> SigningKeyResolver:
    _, pub_pem = rsa_keypair

    class _Stub:
        def get_signing_key_from_jwt(self, _token: str) -> PyJWK:
            return PyJWK(_pem_to_jwk(pub_pem))

    return _Stub()


def _pem_to_jwk(pub_pem: bytes) -> dict[str, Any]:
    """Convert a PEM-encoded public key into a minimal RSA JWK dict."""
    import base64

    pub = serialization.load_pem_public_key(pub_pem)
    numbers = pub.public_numbers()  # type: ignore[attr-defined]

    def _b64u(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": "test-key",
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }


@pytest.fixture
def make_token(rsa_keypair: tuple[bytes, bytes]) -> Callable[..., str]:
    priv_pem, _ = rsa_keypair

    def _build(**overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": EXPECTED_ISSUER,
            "aud": APP_ARI,
            "iat": now,
            "nbf": now,
            "exp": now + 60,
            "jti": "test-jti",
            "app": {"installationId": INSTALL_ARI},
            "context": {"cloudId": CLOUD_ID},
        }
        # Allow overrides to either replace or remove (sentinel = None means delete).
        for key, value in overrides.items():
            if value is _DELETE:
                claims.pop(key, None)
            else:
                claims[key] = value
        return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": "test-key"})

    return _build


_DELETE = object()


# ----- happy path ----------------------------------------------------------


def test_verify_fit_returns_context(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    token = make_token()
    ctx = verify_fit(token, expected_audience=APP_ARI, resolver=stub_resolver)
    assert ctx.cloud_id == CLOUD_ID
    assert ctx.installation_id == INSTALL_ARI
    assert ctx.app_id == APP_ARI


# ----- failure modes -------------------------------------------------------


def test_verify_fit_rejects_expired_token(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    now = int(time.time())
    token = make_token(iat=now - 600, nbf=now - 600, exp=now - 60)
    with pytest.raises(ForgeAuthError, match="expired"):
        verify_fit(token, expected_audience=APP_ARI, resolver=stub_resolver)


def test_verify_fit_rejects_wrong_audience(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    token = make_token(aud="ari:cloud:ecosystem::app/some-other-app")
    with pytest.raises(ForgeAuthError, match="Audience mismatch"):
        verify_fit(token, expected_audience=APP_ARI, resolver=stub_resolver)


def test_verify_fit_rejects_wrong_issuer(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    token = make_token(iss="forge/something-else")
    with pytest.raises(ForgeAuthError, match="Issuer mismatch"):
        verify_fit(token, expected_audience=APP_ARI, resolver=stub_resolver)


def test_verify_fit_rejects_missing_required_claim(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    token = make_token(jti=_DELETE)
    with pytest.raises(ForgeAuthError, match="Missing required claim"):
        verify_fit(token, expected_audience=APP_ARI, resolver=stub_resolver)


def test_verify_fit_rejects_missing_cloud_id(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    token = make_token(context={})
    with pytest.raises(ForgeAuthError, match="cloudId"):
        verify_fit(token, expected_audience=APP_ARI, resolver=stub_resolver)


def test_verify_fit_rejects_missing_installation_id(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    token = make_token(app={})
    with pytest.raises(ForgeAuthError, match="installationId"):
        verify_fit(token, expected_audience=APP_ARI, resolver=stub_resolver)


def test_verify_fit_rejects_unconfigured_audience(
    make_token: Callable[..., str], stub_resolver: SigningKeyResolver
) -> None:
    token = make_token()
    with pytest.raises(ForgeAuthError, match="not configured"):
        verify_fit(token, expected_audience="", resolver=stub_resolver)


def test_verify_fit_propagates_jwks_failure(make_token: Callable[..., str]) -> None:
    class _BrokenResolver:
        def get_signing_key_from_jwt(self, _token: str) -> PyJWK:
            raise jwt.PyJWKClientError("kid not found")

    token = make_token()
    with pytest.raises(ForgeAuthError, match="JWKS lookup failed"):
        verify_fit(token, expected_audience=APP_ARI, resolver=_BrokenResolver())


def test_verify_fit_rejects_malformed_token(stub_resolver: SigningKeyResolver) -> None:
    with pytest.raises(ForgeAuthError):
        verify_fit("not.a.jwt", expected_audience=APP_ARI, resolver=stub_resolver)


# ----- static-file JWKS resolver (deployed-env path) -----------------------


def test_static_file_resolver_round_trips_a_token(
    rsa_keypair: tuple[bytes, bytes],
    make_token: Callable[..., str],
    tmp_path: Any,
) -> None:
    """The bake-into-container path: write a JWKS file, point the resolver at
    it, verify a token signed by the matching private key validates."""
    from app.forge.fit_auth import _StaticFileJwkResolver

    _, pub_pem = rsa_keypair
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text('{"keys": [' + str(_pem_to_jwk(pub_pem)).replace("'", '"') + "]}")

    resolver = _StaticFileJwkResolver(jwks_path)
    token = make_token()
    ctx = verify_fit(token, expected_audience=APP_ARI, resolver=resolver)
    assert ctx.cloud_id == CLOUD_ID


def test_static_file_resolver_rejects_unknown_kid_when_refresh_disabled(
    rsa_keypair: tuple[bytes, bytes],
    tmp_path: Any,
) -> None:
    """With live refresh disabled, an unknown kid produces a clear error that
    names the static path and tells the operator a redeploy is the fallback."""
    import jwt as _jwt

    from app.forge.fit_auth import _StaticFileJwkResolver

    _, pub_pem = rsa_keypair
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text('{"keys": [' + str(_pem_to_jwk(pub_pem)).replace("'", '"') + "]}")
    resolver = _StaticFileJwkResolver(jwks_path, live_refresh_url=None)

    # Forge a token with a different kid.
    priv_pem, _ = rsa_keypair
    token = _jwt.encode(
        {"iss": EXPECTED_ISSUER, "aud": APP_ARI, "iat": 0, "nbf": 0, "exp": 9999999999, "jti": "j"},
        priv_pem,
        algorithm="RS256",
        headers={"kid": "rotated-out"},
    )
    with pytest.raises(_jwt.PyJWKClientError, match="live refresh disabled"):
        resolver.get_signing_key_from_jwt(token)


def test_static_file_resolver_errors_on_empty_document(tmp_path: Any) -> None:
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text('{"keys": []}')
    from app.forge.fit_auth import _StaticFileJwkResolver

    with pytest.raises(ForgeAuthError, match="No usable keys"):
        _StaticFileJwkResolver(jwks_path)


# ----- live JWKS refresh on cache miss (ADR-0029) -------------------------


def _jwks_payload(jwks_dicts: list[dict[str, Any]]) -> bytes:
    return json.dumps({"keys": jwks_dicts}).encode()


def _install_urlopen_mock(
    monkeypatch: pytest.MonkeyPatch, payloads: list[bytes | Exception]
) -> list[str]:
    """Replace urllib.request.urlopen with a stub that yields `payloads` in order.

    Returns a list that records the URL of each call, so tests can assert the
    refresh fired exactly N times.
    """
    from contextlib import contextmanager

    calls: list[str] = []
    iterator = iter(payloads)

    @contextmanager
    def _fake_urlopen(url: str, timeout: float = 0):
        calls.append(url)
        try:
            payload = next(iterator)
        except StopIteration:
            raise AssertionError(  # noqa: B904
                "urlopen called more times than the test prepared for"
            )
        if isinstance(payload, Exception):
            raise payload

        class _Resp:
            def read(self_inner) -> bytes:
                return payload

        yield _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return calls


def test_static_file_resolver_refreshes_on_unknown_kid(
    rsa_keypair: tuple[bytes, bytes],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token with a kid not in the baked JWKS triggers a live fetch from
    Atlassian; the rotated key is merged in and the lookup succeeds.

    This is the core ADR-0029 self-healing path: customer-visible rotation
    events resolve in a single request instead of requiring a deploy."""
    import jwt as _jwt

    from app.forge.fit_auth import _StaticFileJwkResolver

    priv_pem, pub_pem = rsa_keypair
    # Bake one key under "original-kid".
    original_jwk = _pem_to_jwk(pub_pem)
    original_jwk["kid"] = "original-kid"
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps({"keys": [original_jwk]}))

    # Live URL will yield a JWKS containing the rotated kid.
    rotated_jwk = _pem_to_jwk(pub_pem)
    rotated_jwk["kid"] = "rotated-kid"
    calls = _install_urlopen_mock(monkeypatch, [_jwks_payload([original_jwk, rotated_jwk])])

    resolver = _StaticFileJwkResolver(jwks_path, live_refresh_url="https://example.test/jwks")

    rotated_token = _jwt.encode(
        {"iss": EXPECTED_ISSUER, "aud": APP_ARI, "iat": 0, "nbf": 0, "exp": 9999999999, "jti": "j"},
        priv_pem,
        algorithm="RS256",
        headers={"kid": "rotated-kid"},
    )
    key = resolver.get_signing_key_from_jwt(rotated_token)
    assert key is not None
    assert calls == ["https://example.test/jwks"]


def test_static_file_resolver_rate_limits_live_refresh(
    rsa_keypair: tuple[bytes, bytes],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst of misses for the same unknown kid hits the live URL once,
    then re-uses the rate-limit window — bounds load on Atlassian if a
    malformed-kid client keeps hammering us."""
    import jwt as _jwt

    from app.forge.fit_auth import _StaticFileJwkResolver

    priv_pem, pub_pem = rsa_keypair
    baked_jwk = _pem_to_jwk(pub_pem)
    baked_jwk["kid"] = "baked"
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps({"keys": [baked_jwk]}))

    # First refresh succeeds but doesn't include the requested kid. Second
    # call MUST be rate-limited (test will fail if urlopen is invoked twice).
    refreshed_jwk = _pem_to_jwk(pub_pem)
    refreshed_jwk["kid"] = "unrelated"
    calls = _install_urlopen_mock(monkeypatch, [_jwks_payload([baked_jwk, refreshed_jwk])])

    resolver = _StaticFileJwkResolver(
        jwks_path,
        live_refresh_url="https://example.test/jwks",
        min_refresh_interval_s=9_999.0,
    )

    bad_token = _jwt.encode(
        {"iss": EXPECTED_ISSUER, "aud": APP_ARI, "iat": 0, "nbf": 0, "exp": 9999999999, "jti": "j"},
        priv_pem,
        algorithm="RS256",
        headers={"kid": "never-existed"},
    )
    for _ in range(3):
        with pytest.raises(_jwt.PyJWKClientError, match="did not yield kid"):
            resolver.get_signing_key_from_jwt(bad_token)
    assert calls == ["https://example.test/jwks"], (
        "rate-limit should suppress the second and third refresh attempts"
    )


def test_static_file_resolver_falls_through_when_live_refresh_fails(
    rsa_keypair: tuple[bytes, bytes],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Atlassian's JWKS endpoint is unreachable, the resolver still raises
    a clear PyJWKClientError naming both the file path and the live URL —
    no silent fallback, no infinite retry."""
    import jwt as _jwt

    from app.forge.fit_auth import _StaticFileJwkResolver

    priv_pem, pub_pem = rsa_keypair
    baked_jwk = _pem_to_jwk(pub_pem)
    baked_jwk["kid"] = "baked"
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps({"keys": [baked_jwk]}))

    _install_urlopen_mock(monkeypatch, [TimeoutError("simulated network timeout")])

    resolver = _StaticFileJwkResolver(jwks_path, live_refresh_url="https://example.test/jwks")
    token = _jwt.encode(
        {"iss": EXPECTED_ISSUER, "aud": APP_ARI, "iat": 0, "nbf": 0, "exp": 9999999999, "jti": "j"},
        priv_pem,
        algorithm="RS256",
        headers={"kid": "rotated-out"},
    )
    with pytest.raises(_jwt.PyJWKClientError, match=r"live refresh from \S+ did not yield kid"):
        resolver.get_signing_key_from_jwt(token)
