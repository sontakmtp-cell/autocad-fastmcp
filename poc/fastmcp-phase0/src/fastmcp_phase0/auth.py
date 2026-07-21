"""Public FastMCP authentication configuration used by the spike."""

from __future__ import annotations

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier


def build_remote_auth(
    *,
    public_key: str | bytes,
    issuer: str,
    audience: str,
    resource_url: str,
) -> RemoteAuthProvider:
    """Build the same resource-server shape the future Gateway will use."""

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
