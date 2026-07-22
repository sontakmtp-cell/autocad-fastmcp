"""Public FastMCP authentication configuration used by the spike."""

from __future__ import annotations

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier


class SubjectJWTVerifier(JWTVerifier):
    """Verify JWT integrity and require a real user subject claim.

    Scope authorization deliberately remains at the FastMCP component boundary.
    A client_id or azp claim is never accepted as a user identity fallback.
    """

    async def verify_token(self, token: str):
        access_token = await super().verify_token(token)
        if access_token is None:
            return None
        subject = access_token.claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            return None
        return access_token


def build_remote_auth(
    *,
    public_key: str | bytes,
    issuer: str,
    audience: str,
    resource_url: str,
) -> RemoteAuthProvider:
    """Build the same resource-server shape the future Gateway will use."""

    server_base_url = resource_url.removesuffix("/mcp")
    verifier = SubjectJWTVerifier(
        public_key=public_key,
        issuer=issuer,
        audience=audience,
        algorithm="RS256",
        base_url=resource_url,
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[issuer],
        base_url=server_base_url,
        resource_base_url=server_base_url,
        scopes_supported=["autocad.read"],
    )
