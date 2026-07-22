from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import site
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from autocad_contracts import (
    AckMessage,
    CommandMessage,
    HelloMessage,
    ProgressMessage,
    ReconcileMessage,
    ReconcileResultMessage,
    ResultMessage,
    WelcomeMessage,
    canonical_capability_hash,
    canonical_json,
    parse_agent_message,
)
from autocad_gateway.app import GatewayConfig, create_app
from autocad_gateway.composition import build_services
from autocad_gateway.app import build_mcp_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _stop_process(
    process: subprocess.Popen, *, timeout: float = 5.0
) -> None:
    """Bound subprocess cleanup so a launcher cannot strand a CI runner."""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.to_thread(process.wait, timeout=timeout)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await asyncio.to_thread(process.wait, timeout=timeout)


async def _read_process_stderr(
    process: subprocess.Popen, *, timeout: float = 2.0
) -> str:
    if process.stderr is None:
        return ""
    try:
        stderr = await asyncio.wait_for(
            asyncio.to_thread(process.stderr.read), timeout=timeout
        )
    except asyncio.TimeoutError:
        return "<stderr read timed out>"
    return stderr.decode(errors="replace")


class LiveFixtureAgent:
    def __init__(self, url: str, device_id: str, token: str, ssl_context=None) -> None:
        self.url = url
        self.device_id = device_id
        self.token = token
        self.ssl_context = ssl_context
        self.commands: list[str] = []
        self._sequence = 0
        self.fixture_variant = 0

    async def run(self) -> None:
        async with websockets.connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.token}"},
            ssl=self.ssl_context,
        ) as websocket:
            hello = HelloMessage(
                device_id=self.device_id,
                fixture_proof=self.token,
                capability_hash=canonical_capability_hash(["observe", "query"]),
                capabilities=["observe", "query"],
                last_processed_sequence=self._sequence,
            )
            await websocket.send(json.dumps(hello.model_dump(mode="json", exclude_none=True)))
            welcome = parse_agent_message(await websocket.recv())
            assert isinstance(welcome, WelcomeMessage)
            while True:
                message = parse_agent_message(await websocket.recv())
                if isinstance(message, CommandMessage):
                    self.commands.append(message.command_id or "")
                    await self._send(websocket, AckMessage(
                        session_id=message.session_id,
                        device_id=self.device_id,
                        job_id=message.job_id,
                        command_id=message.command_id,
                        sequence=self._next(),
                        status="accepted",
                        idempotency_key=message.idempotency_key,
                        payload_hash=message.payload_hash,
                    ))
                    await self._send(websocket, ProgressMessage(
                        session_id=message.session_id,
                        device_id=self.device_id,
                        job_id=message.job_id,
                        command_id=message.command_id,
                        sequence=self._next(),
                        payload_hash=message.payload_hash,
                        phase="inspect",
                        percent=50,
                        message="fixture inspect",
                    ))
                    entities = [{
                        "entity_id": "E1",
                        "entity_type": "Line",
                        "layer": "0",
                        "geometry": {
                            "start": [self.fixture_variant, 0],
                            "end": [10 + self.fixture_variant, 0],
                        },
                    }]
                    drawing = {"entity_count": 1, "layers": ["0"], "name": self.device_id}
                    revision = hashlib.sha256(
                        canonical_json({"drawing": drawing, "entities": entities}).encode()
                    ).hexdigest()
                    snapshot = {
                        "snapshot_id": f"snapshot-{message.job_id}",
                        "document_revision": revision,
                        "observation_level": message.payload.get("observation_level", "summary"),
                        "drawing": drawing,
                        "entity_summary": {"LINE": 1},
                        "entities": entities,
                    }
                    await self._send(websocket, ProgressMessage(
                        session_id=message.session_id,
                        device_id=self.device_id,
                        job_id=message.job_id,
                        command_id=message.command_id,
                        sequence=self._next(),
                        payload_hash=message.payload_hash,
                        phase="complete",
                        percent=100,
                        message="fixture complete",
                    ))
                    await self._send(websocket, ResultMessage(
                        session_id=message.session_id,
                        device_id=self.device_id,
                        job_id=message.job_id,
                        command_id=message.command_id,
                        sequence=self._next(),
                        status="succeeded",
                        payload_hash=message.payload_hash,
                        result={"snapshot": snapshot},
                    ))
                elif isinstance(message, ReconcileMessage):
                    for command in message.commands:
                        await self._send(websocket, ReconcileResultMessage(
                            session_id=message.session_id,
                            device_id=self.device_id,
                            job_id=command.job_id,
                            command_id=command.command_id,
                            sequence=self._next(),
                            status="not_started",
                            payload_hash=command.payload_hash,
                        ))

    def _next(self) -> int:
        self._sequence += 1
        return self._sequence

    @staticmethod
    async def _send(websocket, message) -> None:
        await websocket.send(json.dumps(message.model_dump(mode="json", exclude_none=True)))


@pytest.mark.asyncio
async def test_phase3_mcp_observe_job_query_and_two_device_routing(tmp_path):
    port = _free_port()
    config = GatewayConfig(
        host="127.0.0.1",
        port=port,
        profile="phase3_poc",
        db_path=str(tmp_path / "phase3.db"),
        fixture_tokens=(("device-a", "token-a"), ("device-b", "token-b")),
        allowed_hosts=("127.0.0.1:*",),
        stateless_http=True,
        command_timeout_seconds=10,
    )
    services = build_services(config)
    app = create_app(services, config=config)
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=port, log_level="error"))
    server_task = asyncio.create_task(server.serve())
    agents = [
        LiveFixtureAgent(f"ws://127.0.0.1:{port}/agent/ws", "device-a", "token-a"),
        LiveFixtureAgent(f"ws://127.0.0.1:{port}/agent/ws", "device-b", "token-b"),
    ]
    agent_tasks: list[asyncio.Task] = []
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        agent_tasks = [asyncio.create_task(agent.run()) for agent in agents]
        for _ in range(100):
            if len(await services.registry.all()) == 2:
                break
            await asyncio.sleep(0.05)
        assert len(await services.registry.all()) == 2
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False
        ) as http_client:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp", http_client=http_client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    devices = await session.call_tool("cad_list_devices", {"online_only": True})
                    assert {item["device_id"] for item in devices.structuredContent["devices"]} == {"device-a", "device-b"}
                    observed = await session.call_tool("cad_observe", {"device_id": "device-a"})
                    assert not observed.isError
                    assert observed.structuredContent["job_id"]
                    job = await session.call_tool(
                        "cad_get_job", {"job_id": observed.structuredContent["job_id"]}
                    )
                    assert job.structuredContent["state"] == "succeeded"
                    assert [event["sequence"] for event in job.structuredContent["events"]] == sorted(
                        event["sequence"] for event in job.structuredContent["events"]
                    )
                    queried = await session.call_tool(
                        "cad_query",
                        {
                            "snapshot_id": observed.structuredContent["snapshot_id"],
                            "types": ["line"],
                        },
                    )
                    assert queried.structuredContent["total"] == 1

                    agents[0].fixture_variant = 1
                    observed_again = await session.call_tool(
                        "cad_observe", {"device_id": "device-a"}
                    )
                    assert not observed_again.isError
                    assert observed_again.structuredContent["job_id"] != observed.structuredContent["job_id"]
                    assert observed_again.structuredContent["snapshot_id"] != observed.structuredContent["snapshot_id"]
                    assert observed_again.structuredContent["document_revision"] != observed.structuredContent["document_revision"]
                    queried_again = await session.call_tool(
                        "cad_query",
                        {
                            "snapshot_id": observed_again.structuredContent["snapshot_id"],
                            "types": ["line"],
                        },
                    )
                    assert queried_again.structuredContent["entities"][0]["geometry"][
                        "start"
                    ] == [1, 0]
                    resource = await session.read_resource(
                        f"cad://jobs/{observed.structuredContent['job_id']}"
                    )
                    assert json.loads(resource.contents[0].text)["state"] == "succeeded"
        assert len(agents[0].commands) == 2
        assert agents[1].commands == []
    finally:
        for task in agent_tasks:
            task.cancel()
        if agent_tasks:
            await asyncio.gather(*agent_tasks, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)


@pytest.mark.asyncio
async def test_phase3_public_surface_matches_additive_snapshots(tmp_path):
    from fastmcp import Client

    services = build_services(
        GatewayConfig(
            profile="phase3_poc",
            db_path=str(tmp_path / "snapshots.db"),
            fixture_tokens=(("device-a", "token-a"),),
        )
    )
    async with Client(build_mcp_server(services)) as client:
        tools = [
            {
                "name": item.name,
                "title": item.title,
                "description": item.description,
                    "annotations": item.annotations.model_dump(mode="json", exclude_none=True),
                "input_schema_sha256": hashlib.sha256(
                    json.dumps(item.inputSchema, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "output_schema_sha256": hashlib.sha256(
                    json.dumps(item.outputSchema, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            }
            for item in await client.list_tools()
        ]
        resources = [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in await client.list_resource_templates()]
    root = Path(__file__).parents[1] / "snapshots"
    assert tools == json.loads((root / "phase3_tools.json").read_text(encoding="utf-8"))
    assert resources == json.loads((root / "phase3_resources.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_phase3_wss_loopback_accepts_test_certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=10))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "localhost.crt"
    key_path = tmp_path / "localhost.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    port = _free_port()
    config = GatewayConfig(
        host="127.0.0.1",
        port=port,
        profile="phase3_poc",
        db_path=str(tmp_path / "wss.db"),
        fixture_tokens=(("device-a", "token-a"),),
        allowed_hosts=("127.0.0.1:*",),
    )
    services = build_services(config)
    app = create_app(services, config=config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=port,
            log_level="error",
            ssl_keyfile=str(key_path),
            ssl_certfile=str(cert_path),
        )
    )
    server_task = asyncio.create_task(server.serve())
    tls = ssl.create_default_context()
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE
    agent = LiveFixtureAgent(f"wss://127.0.0.1:{port}/agent/ws", "device-a", "token-a", tls)
    agent_task = None
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        agent_task = asyncio.create_task(agent.run())
        for _ in range(200):
            if len(await services.registry.all()) == 1:
                break
            if agent_task.done():
                agent_task.result()
            await asyncio.sleep(0.05)
        assert len(await services.registry.all()) == 1
    finally:
        if agent_task is not None:
            agent_task.cancel()
            await asyncio.gather(agent_task, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)


@pytest.mark.asyncio
async def test_phase3_standalone_simulator_processes_complete_mcp_flow(tmp_path):
    project_root = Path(__file__).parents[3]
    simulator_project = project_root / "poc" / "phase3-simulated-agent"
    port = _free_port()
    config = GatewayConfig(
        host="127.0.0.1",
        port=port,
        profile="phase3_poc",
        db_path=str(tmp_path / "processes.db"),
        fixture_tokens=(("device-a", "token-a"), ("device-b", "token-b")),
        allowed_hosts=("127.0.0.1:*",),
        stateless_http=True,
        command_timeout_seconds=15,
    )
    services = build_services(config)
    server = uvicorn.Server(uvicorn.Config(create_app(services, config=config), host=config.host, port=port, log_level="error"))
    server_task = asyncio.create_task(server.serve())
    processes = []
    simulator_environment = os.environ.copy()
    simulator_python = Path(getattr(sys, "_base_executable", sys.executable))
    assert simulator_python.is_file()
    python_paths = [
        str(simulator_project / "src"),
        str(project_root / "packages" / "contracts" / "src"),
        *site.getsitepackages(),
    ]
    inherited_pythonpath = simulator_environment.get("PYTHONPATH")
    simulator_environment["PYTHONPATH"] = (
        os.pathsep.join(python_paths)
        if not inherited_pythonpath
        else os.pathsep.join([*python_paths, inherited_pythonpath])
    )
    for proxy_name in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        simulator_environment.pop(proxy_name, None)
    simulator_environment["NO_PROXY"] = "127.0.0.1,localhost"
    simulator_environment["no_proxy"] = "127.0.0.1,localhost"
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        for device_id, token in config.fixture_tokens:
            processes.append(
                subprocess.Popen(
                    [
                        str(simulator_python),
                        "-m",
                        "autocad_phase3_sim_agent",
                        "--url",
                        f"ws://127.0.0.1:{port}/agent/ws",
                        "--device-id",
                        device_id,
                        "--token",
                        token,
                        "--stop-after-terminal",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env=simulator_environment,
                )
            )
        for _ in range(200):
            if len(await services.registry.all()) == 2:
                break
            await asyncio.sleep(0.05)
        connected = len(await services.registry.all())
        if connected != 2:
            await asyncio.gather(
                *(_stop_process(process) for process in processes),
                return_exceptions=True,
            )
            diagnostics = []
            for process in processes:
                stderr = await _read_process_stderr(process)
                diagnostics.append(
                    f"returncode={process.returncode}: {stderr[-2000:]}"
                )
            pytest.fail(
                f"only {connected} simulator Agents connected; "
                + " | ".join(diagnostics)
            )
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False
        ) as http_client:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp", http_client=http_client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    for device_id in ("device-a", "device-b"):
                        result = await session.call_tool(
                            "cad_observe", {"device_id": device_id}
                        )
                        assert not result.isError
                        assert result.structuredContent["job_id"]
    finally:
        await asyncio.gather(
            *(_stop_process(process) for process in processes),
            return_exceptions=True,
        )
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
