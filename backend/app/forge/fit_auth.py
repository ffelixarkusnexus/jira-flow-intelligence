"""Forge Invocation Token (FIT) verification.

Forge calls our backend (a "Forge Remote") with a JWT signed by Atlassian
in the `Authorization: Bearer <token>` header. The token is asymmetrically
signed; we validate against Atlassian's public JWKS.

Spec: https://developer.atlassian.com/platform/forge/remote/essentials/

Key invariants enforced here:
- Signature verified via JWKS lookup keyed on the token's `kid`.
- `iss` must equal the literal `forge/invocation-token`.
- `aud` must equal our app ARI (passed in by the caller; we don't hard-code
  it because the same code runs in dev/staging/prod with different App IDs).
- `exp`, `iat`, `nbf`, `jti` must all be present.

Replaces ADR-0016's HS256/qsh-based verification with the asymmetric path
required by Forge (ADR-0019).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import jwt
from jwt import PyJWK, PyJWKClient

from app.core.logging import get_logger

logger = get_logger(__name__)


JWKS_URL = "https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json"
EXPECTED_ISSUER = "forge/invocation-token"
ALGORITHMS = ("RS256",)
REQUIRED_CLAIMS = ("exp", "iat", "nbf", "aud", "iss", "jti")

# When set, fit_auth reads the JWKS from this local file as the cold-start
# cache. The Dockerfile bakes it so a fresh container can validate FITs
# without any network call. Rotations are handled live by the resolver
# (ADR-0029); the baked file just bounds cold-start latency. Empty string =
# fall back to PyJWKClient hitting the live URL directly (local dev / tests).
JWKS_FILE_ENV = "FORGE_JWKS_PATH"


class ForgeAuthError(Exception):
    """FIT verification failed."""


@dataclass(frozen=True)
class ForgeContext:
    """Identity bound to a verified Forge Invocation Token.

    `cloud_id` and `installation_id` are what we key tenant lookups on
    (see ADR-0019 — they replace the Connect `client_key`).
    """

    cloud_id: str
    installation_id: str
    app_id: str


class SigningKeyResolver(Protocol):
    """Anything that maps a JWT to its signing PyJWK.

    `PyJWKClient` satisfies this. Tests inject a stub that returns a
    pre-loaded key without hitting the network.
    """

    def get_signing_key_from_jwt(self, token: str) -> PyJWK: ...


class _StaticFileJwkResolver:
    """Reads a JWKS document from a local JSON file and looks up keys by kid.

    The Dockerfile downloads the JWKS at build time so cold-start works
    without any network call. On a cache miss caused by Atlassian rotating a
    signing key, the resolver attempts a single rate-limited live refresh
    against Atlassian's JWKS URL and retries the lookup — see ADR-0029. This
    self-heals key rotations without requiring a redeploy.

    Pass `live_refresh_url=None` to suppress the refresh (useful for tests
    and any environment without public-internet egress).
    """

    def __init__(
        self,
        path: Path,
        *,
        live_refresh_url: str | None = JWKS_URL,
        min_refresh_interval_s: float = 60.0,
        refresh_timeout_s: float = 5.0,
    ) -> None:
        self._keys: dict[str, PyJWK] = {}
        self._path = path
        self._live_refresh_url = (live_refresh_url or "").strip() or None
        self._min_refresh_interval_s = min_refresh_interval_s
        self._refresh_timeout_s = refresh_timeout_s
        self._lock = threading.Lock()
        # Sentinel that always passes the rate-limit check on first miss.
        self._last_refresh_attempt: float = float("-inf")
        document = json.loads(path.read_text())
        self._merge_keys(document)
        if not self._keys:
            raise ForgeAuthError(f"No usable keys in JWKS at {path}")

    def _merge_keys(self, document: dict[str, Any]) -> int:
        """Add any keys from `document` not already cached. Returns count added."""
        added = 0
        for jwk in document.get("keys", []):
            kid = jwk.get("kid")
            if not kid or kid in self._keys:
                continue
            self._keys[kid] = PyJWK(jwk)
            added += 1
        return added

    def _maybe_refresh(self) -> None:
        """Best-effort live JWKS fetch. Caller must hold `self._lock`."""
        if not self._live_refresh_url:
            return
        now = time.monotonic()
        if now - self._last_refresh_attempt < self._min_refresh_interval_s:
            return
        self._last_refresh_attempt = now
        try:
            with urllib.request.urlopen(
                self._live_refresh_url, timeout=self._refresh_timeout_s
            ) as response:
                document = json.loads(response.read())
        except Exception as exc:
            logger.warning("JWKS live refresh from %s failed: %s", self._live_refresh_url, exc)
            return
        added = self._merge_keys(document)
        logger.info(
            "JWKS live refresh from %s added %d new key(s)",
            self._live_refresh_url,
            added,
        )

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.DecodeError as exc:
            raise jwt.PyJWKClientError(f"Malformed token header: {exc}") from exc
        kid = unverified_header.get("kid")
        if not kid:
            raise jwt.PyJWKClientError("Token missing kid")

        # Steady-state fast path: CPython single-key dict reads are atomic.
        key = self._keys.get(kid)
        if key is not None:
            return key

        # Cache miss: take the lock, re-check (another thread may have just
        # refreshed), then attempt a live refresh if still missing.
        with self._lock:
            key = self._keys.get(kid)
            if key is None:
                self._maybe_refresh()
                key = self._keys.get(kid)

        if key is None:
            url_hint = (
                f"; live refresh from {self._live_refresh_url} did not yield kid"
                if self._live_refresh_url
                else "; live refresh disabled, redeploy to refresh"
            )
            raise jwt.PyJWKClientError(f"kid {kid} not in cached JWKS at {self._path}{url_hint}")
        return key


_jwk_client_singleton: SigningKeyResolver | None = None


def get_jwk_client() -> SigningKeyResolver:
    """Lazy singleton JWKS resolver.

    Strategy:
    - If FORGE_JWKS_PATH points at a readable file, use the static resolver
      (deployed environments without public-internet egress).
    - Otherwise, fall back to PyJWKClient hitting Atlassian's URL directly
      (local dev + tests on a network with public egress).
    """
    global _jwk_client_singleton
    if _jwk_client_singleton is not None:
        return _jwk_client_singleton

    static_path = os.environ.get(JWKS_FILE_ENV, "").strip()
    if static_path:
        path = Path(static_path)
        if path.is_file():
            logger.info("FIT auth using static JWKS at %s", path)
            _jwk_client_singleton = _StaticFileJwkResolver(path)
            return _jwk_client_singleton
        logger.warning("FORGE_JWKS_PATH=%s not found; falling back to live URL", static_path)

    _jwk_client_singleton = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=300)
    return _jwk_client_singleton


def verify_fit(
    token: str,
    *,
    expected_audience: str,
    resolver: SigningKeyResolver | None = None,
) -> ForgeContext:
    """Verify a Forge Invocation Token. Raises `ForgeAuthError` on failure."""
    if not expected_audience:
        # Misconfiguration — fail loudly rather than silently accept any audience.
        raise ForgeAuthError("FORGE_APP_ID not configured")

    key_resolver = resolver or get_jwk_client()
    try:
        signing_key = key_resolver.get_signing_key_from_jwt(token)
    except jwt.PyJWKClientError as exc:
        raise ForgeAuthError(f"JWKS lookup failed: {exc}") from exc
    except jwt.DecodeError as exc:
        raise ForgeAuthError(f"Malformed token: {exc}") from exc

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(ALGORITHMS),
            audience=expected_audience,
            issuer=EXPECTED_ISSUER,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ForgeAuthError("Token expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise ForgeAuthError("Audience mismatch") from exc
    except jwt.InvalidIssuerError as exc:
        raise ForgeAuthError("Issuer mismatch") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise ForgeAuthError(f"Missing required claim: {exc.claim}") from exc
    except jwt.InvalidTokenError as exc:
        raise ForgeAuthError(f"Invalid token: {exc}") from exc

    return _extract_context(claims)


def _extract_context(claims: dict[str, Any]) -> ForgeContext:
    """Pull the bits we need out of the verified claims.

    Forge nests identity under `app.installationId` (full ARI) and
    `context.cloudId`. We carry both as raw strings; tenant lookup
    keys on `(cloud_id, installation_id)`.
    """
    app_block = claims.get("app") or {}
    context_block = claims.get("context") or {}
    installation_id = app_block.get("installationId")
    cloud_id = context_block.get("cloudId")
    aud = claims.get("aud")
    app_id = aud[0] if isinstance(aud, list) and aud else aud

    if not isinstance(installation_id, str) or not installation_id:
        raise ForgeAuthError("Missing app.installationId")
    if not isinstance(cloud_id, str) or not cloud_id:
        raise ForgeAuthError("Missing context.cloudId")
    if not isinstance(app_id, str) or not app_id:
        raise ForgeAuthError("Missing aud")

    return ForgeContext(cloud_id=cloud_id, installation_id=installation_id, app_id=app_id)
