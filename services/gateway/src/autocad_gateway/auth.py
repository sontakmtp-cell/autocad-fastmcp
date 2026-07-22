"""JWT fixture auth for boundary tests; Phase 2 has no production OAuth."""

from __future__ import annotations

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier


def build_fixture_auth(
    *, public_key: str | bytes, issuer: str, audience: str, resource_url: str
) -> RemoteAuthProvider:
    server_base_url = resource_url.removesuffix("/mcp")
    verifier = JWTVerifier(
        public_key=public_key,
        issuer=issuer,
        audience=audience,
        algorithm="RS256",
        required_scopes=["autocad.read"],
        base_url=resource_url,
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[issuer],
        base_url=server_base_url,
        resource_base_url=server_base_url,
        scopes_supported=["autocad.read"],
    )


def build_phase4_auth(
    *, issuer: str, audience: str, jwks_uri: str, public_origin: str
) -> RemoteAuthProvider:
    """Build the fail-closed Auth0-compatible verifier for the C1 profile."""

    origin = public_origin.rstrip("/")
    verifier = JWTVerifier(
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=audience,
        algorithm="RS256",
        required_scopes=["autocad.read"],
        base_url=origin,
        ssrf_safe=True,
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[issuer],
        base_url=origin,
        resource_base_url=origin,
        scopes_supported=["autocad.read"],
        resource_name="Kỹ Thuật Vàng AutoCAD",
    )
