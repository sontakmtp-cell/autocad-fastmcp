"""Verify the phase9_workflow full gateway registers scene + workflow tools."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parents[1]


def configure_env() -> None:
    os.environ["AUTOCAD_MCP_GATEWAY_PROFILE"] = "phase9_workflow"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_HOST"] = "127.0.0.1"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_PORT"] = "8765"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_PATH"] = "/mcp"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP"] = "0"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS"] = "127.0.0.1:8765;localhost:8765"
    os.environ["AUTOCAD_MCP_PHASE7_DB_PATH"] = str(
        Path(os.environ["LOCALAPPDATA"])
        / "Kythuatvang"
        / "AutoCADGateway"
        / "phase6-program.sqlite3"
    )
    os.environ["AUTOCAD_MCP_PHASE4_OAUTH_ISSUER"] = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/"
    os.environ["AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE"] = "https://cad.kythuatvang.com/mcp"
    os.environ["AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI"] = (
        "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/.well-known/jwks.json"
    )
    os.environ["AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN"] = "https://cad.kythuatvang.com"
    os.environ["AUTOCAD_MCP_PHASE4_WRITE_DISABLED"] = "1"
    os.environ["AUTOCAD_MCP_PROGRAM_V0_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_MANAGED_WRITE_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS"] = (
        "device-2d951d33-6fbb-49ca-ba22-95dfabd1ef78"
    )
    os.environ["AUTOCAD_MCP_PHASE6_POLICY_VERSION"] = "phase6-policy/1"
    os.environ["AUTOCAD_MCP_PHASE7_C2_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_TRUSTED_APPROVAL_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_DEVICE_LOCAL_APPROVAL_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PORTAL_RECENT_AUTH_APPROVAL_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_RECOVERY_CASES_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PUBLIC_ROLLBACK_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PROGRAM_V1_COMPILER_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_CHECKPOINT_V2_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST"] = (
        "compiler.core/1,create-equivalent/1,transform.exact/1"
    )
    os.environ["AUTOCAD_MCP_PHASE8_ROLLOUT_POLICY_EPOCH"] = "1"
    os.environ["AUTOCAD_MCP_PHASE8_COMPILER_PACKAGE_HASH"] = (
        "sha256:bfca03d31790509b0fd986efb9f0a36ea5d0f9655c7d9115e6f1a68e7ae09eae"
    )
    os.environ["AUTOCAD_MCP_PHASE8_PACKAGE_ID"] = "autocad.managed_host.r25"
    os.environ["AUTOCAD_MCP_PHASE8_PACKAGE_VERSION"] = "0.8.0"
    os.environ["AUTOCAD_MCP_PHASE8_PACKAGE_HASH"] = (
        "sha256:dc8a46c57a9aa437a55d3eacb66769dbbd862d78f3aa4a220c9b522a2b83dff5"
    )
    os.environ["AUTOCAD_MCP_PHASE8_CAPABILITY_MANIFEST_HASH"] = (
        "sha256:661c24d3da05ea15e0c773a9fb8128c3412805a5d9fdf9da069ec51c5c212873"
    )
    os.environ["AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_VERSION"] = "cad.operation-registry/1"
    os.environ["AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_HASH"] = (
        "sha256:1b840d43a4872322882f4443c07fb0f0b238cbb1d122cbefb4fe7e59097024a5"
    )
    os.environ["AUTOCAD_MCP_PHASE8_POLICY_VERSION"] = "phase8-policy/1"
    os.environ["AUTOCAD_MCP_PHASE9_SKILL_CATALOG_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE9_WORKFLOW_ENGINE_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE9_PUBLIC_WORKFLOW_TOOLS_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE9_CLEANUP_AUDIT_SKILL_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE9_AUTO_DIMENSION_SKILL_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE9_WRITE_WORKFLOWS_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE9_POLICY_EPOCH"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_SCENE_ENGINE_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_PUBLIC_SCENE_TOOLS_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_SCENE_RESOURCES_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_MECHANICAL_FEATURES_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_ANNOTATION_LINKS_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_WORKFLOW_SCENE_STEPS_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_PORTAL_SCENE_VIEWS_ENABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET"] = (
        "p6o7dHmRJ3vN8u2xKqY5bWcZfTgE4sAaBwL0jPqR7nVcXyDkMhG2uS9tQzF1eNf"
    )


async def main() -> None:
    configure_env()
    sys.path.insert(0, str(REPO_ROOT / "services" / "gateway" / "src"))
    from autocad_gateway.app import GatewayConfig, build_mcp_server
    from autocad_gateway.composition import build_services

    config = GatewayConfig.from_env().validate()
    services = build_services(config)
    await services.initialize()
    try:
        async with Client(build_mcp_server(services)) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            resources = {
                t.uriTemplate for t in await client.list_resource_templates()
            }
            print("TOOLS:", sorted(tools))
            print("SCENE:", sorted(n for n in tools if "scene" in n))
            print("WORKFLOW:", sorted(n for n in tools if "workflow" in n or "skill" in n))
            print("PROGRAM:", sorted(n for n in tools if "program" in n))
            print("SCENE_RESOURCES:", sorted(r for r in resources if "scenes" in r))
    finally:
        await services.database.close()


if __name__ == "__main__":
    asyncio.run(main())
