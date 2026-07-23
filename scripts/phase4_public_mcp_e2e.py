"""Run the Phase 4 public MCP read flow with a real OAuth protocol client.

Tokens and dynamically registered client information stay in memory only.
The script prints the authorization URL, waits for the loopback callback, then
runs initialize -> tools/list -> cad_list_devices -> cad_observe -> cad_get_job.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)


class MemoryStorage:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        self.client_info = client_info


async def run(endpoint: str, device_id: str, timeout_seconds: float) -> None:
    loop = asyncio.get_running_loop()
    callback: asyncio.Future[tuple[str, str | None]] = loop.create_future()

    async def handle_callback(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_line = (await reader.readline()).decode("ascii", errors="replace")
        target = request_line.split(" ", 2)[1] if " " in request_line else "/"
        params = parse_qs(urlsplit(target).query)
        code = params.get("code", [""])[0]
        state = params.get("state", [None])[0]
        body = (
            "Phase 4 OAuth completed. You can close this tab."
            if code
            else "Phase 4 OAuth failed. Return to the protocol client log."
        )
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            "Connection: close\r\n\r\n"
            f"{body}"
        )
        writer.write(response.encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        if not callback.done():
            callback.set_result((code, state))

    server = await asyncio.start_server(handle_callback, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"

    async def redirect_handler(url: str) -> None:
        print(f"AUTHORIZATION_URL={url}", flush=True)

    async def callback_handler() -> tuple[str, str | None]:
        return await asyncio.wait_for(callback, timeout=timeout_seconds)

    auth = OAuthClientProvider(
        endpoint,
        OAuthClientMetadata(
            redirect_uris=[redirect_uri],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="autocad.read",
            client_name="AutoCAD MCP Phase 4 protocol-client evidence",
            software_id="autocad-mcp-phase4-e2e",
            software_version="1",
        ),
        MemoryStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=timeout_seconds,
    )

    try:
        async with httpx.AsyncClient(
            auth=auth,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds, read=timeout_seconds),
        ) as http_client:
            async with streamable_http_client(
                endpoint, http_client=http_client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool_names = [tool.name for tool in tools.tools]
                    devices = await session.call_tool(
                        "cad_list_devices", {"online_only": True}
                    )
                    online = devices.structuredContent["devices"]
                    if not any(item["device_id"] == device_id for item in online):
                        raise RuntimeError(
                            f"device {device_id!r} is not online for this principal"
                        )
                    observed = await session.call_tool(
                        "cad_observe",
                        {
                            "device_id": device_id,
                            "observation_level": "summary",
                            "include_preview_image": False,
                        },
                    )
                    if observed.isError:
                        raise RuntimeError("cad_observe returned an MCP tool error")
                    job_id = observed.structuredContent["job_id"]
                    job = None
                    deadline = loop.time() + timeout_seconds
                    while loop.time() < deadline:
                        job = await session.call_tool(
                            "cad_get_job", {"job_id": job_id}
                        )
                        if job.structuredContent["state"] in {
                            "succeeded",
                            "failed",
                            "cancelled",
                            "needs_attention",
                        }:
                            break
                        await asyncio.sleep(0.25)
                    if job is None or job.structuredContent["state"] != "succeeded":
                        state = (
                            None if job is None else job.structuredContent["state"]
                        )
                        raise RuntimeError(f"observation job ended as {state!r}")

                    payload = job.structuredContent
                    summary = (
                        payload.get("result", {})
                        .get("snapshot", {})
                        .get("drawing", {})
                    )
                    print(
                        json.dumps(
                            {
                                "public_mcp_e2e": "PASS",
                                "tools": tool_names,
                                "device_id": device_id,
                                "job_id": job_id,
                                "snapshot_id": observed.structuredContent.get(
                                    "snapshot_id"
                                ),
                                "document_name": summary.get("document_name"),
                                "entity_count": summary.get("entity_count"),
                                "layers": summary.get("layers"),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        flush=True,
                    )
    finally:
        server.close()
        await server.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint", default="https://cad.kythuatvang.com/mcp"
    )
    parser.add_argument("--device-id", default="autocad-lab-01")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    asyncio.run(run(args.endpoint, args.device_id, args.timeout))


if __name__ == "__main__":
    main()
