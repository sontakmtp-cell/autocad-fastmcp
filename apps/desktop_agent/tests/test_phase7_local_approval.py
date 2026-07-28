from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from autocad_contracts.agent_protocol import (
    ApprovalDecisionMessage,
    ApprovalRequestMessage,
    ProgramCommandMessage,
    WelcomeMessage,
    approval_decision_proof_payload,
    approval_request_digest,
    message_dict,
    parse_agent_message,
)
from autocad_desktop_agent.approval import ApprovalConflict, ApprovalStore
from autocad_desktop_agent.config import AgentConfig, IdentityMode
from autocad_desktop_agent.core import AgentCore
from autocad_desktop_agent.ledger import CommandLedger
from autocad_desktop_agent.pairing import DeviceIdentityStore
from autocad_desktop_agent.state import RuntimeState


class PlainProtector:
    def protect(self, value: bytes) -> bytes:
        return b"protected:" + value

    def unprotect(self, value: bytes) -> bytes:
        assert value.startswith(b"protected:")
        return value.removeprefix(b"protected:")


class Credentials:
    protocol_version = "cad.agent/2"

    async def load(self) -> str:
        return "short-lived-token"

    def hello_proof(self, message_id: str, token: str) -> str:
        return "proof"


class Executor:
    async def probe(self):
        return type(
            "Presence",
            (),
            {
                "runtime_state": "online_idle",
                "autocad_state": "Sẵn sàng",
                "document_name": "drawing33.dwg",
            },
        )()


class Socket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, value: str) -> None:
        self.sent.append(value)


class SessionSocket(Socket):
    async def recv(self) -> str:
        return json.dumps(
            message_dict(
                WelcomeMessage(
                    protocol_version="cad.agent/2",
                    session_id="session-current",
                    selected_version="cad.agent/2",
                )
            )
        )

    def __aiter__(self):
        async def empty():
            if False:
                yield ""

        return empty()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_core(
    tmp_path: Path,
    *,
    enabled: bool = True,
) -> tuple[AgentCore, DeviceIdentityStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    package = tmp_path / "mcp_dispatch.lsp"
    package.write_text("(princ)", encoding="utf-8")
    package_hash = hashlib.sha256(package.read_bytes()).hexdigest()
    identity_store = DeviceIdentityStore(
        tmp_path / "identity",
        protector=PlainProtector(),
    )
    identity = identity_store.ensure()
    identity_store.mark_paired()
    config = AgentConfig(
        gateway_ws_url="ws://127.0.0.1/agent/ws",
        gateway_http_url="http://127.0.0.1",
        portal_url="http://127.0.0.1",
        device_id=identity.device_id,
        device_name="Máy kiểm thử",
        ledger_path=tmp_path / "agent.db",
        package_path=package,
        package_sha256=package_hash,
        identity_mode=IdentityMode.BROWSER_PAIRING,
        phase7_c2_enabled=enabled,
        trusted_approval_enabled=enabled,
        device_local_approval_enabled=enabled,
    )
    core = AgentCore(
        config,
        Credentials(),
        CommandLedger(config.ledger_path),
        Executor(),
        identity_store=identity_store,
    )
    return core, identity_store


def make_request(
    identity_store: DeviceIdentityStore,
    **updates: object,
) -> ApprovalRequestMessage:
    identity = identity_store.load_identity()
    now = datetime.now(timezone.utc)
    value: dict[str, object] = {
        "protocol_version": "cad.agent/2",
        "message_type": "approval_request",
        "message_id": "approval-message-1",
        "session_id": "session-current",
        "device_id": identity.device_id,
        "sequence": 1,
        "issued_at": now.isoformat(),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
        "approval_request_id": "approval-request-1",
        "intent_id": "intent-1",
        "consent_id": "consent-1",
        "intent_digest": "sha256:" + "a" * 64,
        "challenge_nonce": "n" * 43,
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "required_assurance": "device_local_confirmation",
        "device_identity_generation": identity.generation,
        "device_key_thumbprint": identity.key_thumbprint,
        "trusted_summary": {
            "operation": "program_commit",
            "operation_summary": "Tạo 3 đường tròn theo preview đã khóa.",
            "document_name": "drawing33.dwg",
            "document_id": "document-1",
            "operation_count": 1,
            "entity_count": 3,
            "runtime_label": "Managed .NET R25",
            "runtime_id": "managed_dotnet",
            "package_id": "autocad.managed_host.r25",
            "package_version": "0.2.0",
            "registry_version": "cad.program/0.2",
            "risk_class": "medium",
            "preview_created_at": now.isoformat(),
            "warnings": [],
            "support_id": "support-1",
        },
    }
    value.update(updates)
    value["approval_request_digest"] = approval_request_digest(value)
    parsed = parse_agent_message(value)
    assert isinstance(parsed, ApprovalRequestMessage)
    return parsed


def bind_session(core: AgentCore, socket: Socket) -> None:
    core._session_id = "session-current"
    core._current_websocket = socket


@pytest.mark.asyncio
async def test_current_exact_device_request_is_rendered_and_signed_decision_only_uses_socket(
    tmp_path: Path,
) -> None:
    core, identity_store = make_core(tmp_path)
    socket = Socket()
    bind_session(core, socket)
    request = make_request(identity_store)

    await core._handle_approval_request(socket, request)
    assert core.view_state.pending_approval_count == 1
    view = core.view_state.pending_approvals[0]
    assert view.document_name == "drawing33.dwg"
    assert view.actionable is True

    decision = await core.submit_approval_decision(
        request.approval_request_id,
        "approve",
    )
    assert isinstance(decision, ApprovalDecisionMessage)
    assert len(socket.sent) == 1
    assert not hasattr(core.executor, "execute")

    identity = identity_store.load_identity()
    proof = approval_decision_proof_payload(
        approval_request_id=decision.approval_request_id,
        approval_request_digest=decision.approval_request_digest,
        session_id=decision.session_id,
        device_id=decision.device_id,
        device_identity_generation=decision.device_identity_generation,
        device_key_thumbprint=decision.device_key_thumbprint,
        consent_id=decision.consent_id,
        intent_id=decision.intent_id,
        intent_digest=decision.intent_digest,
        challenge_nonce=decision.challenge_nonce,
        decision=decision.decision,
        decided_at=decision.decided_at,
    )
    Ed25519PublicKey.from_public_bytes(_decode(identity.public_key)).verify(
        _decode(decision.device_session_proof),
        proof.encode("utf-8"),
    )
    assert core.view_state.pending_approval_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", "session-replaced"),
        ("device_id", "device-wrong"),
        ("device_identity_generation", 2),
        ("device_key_thumbprint", "sha256:" + "c" * 64),
    ],
)
async def test_wrong_session_device_key_or_generation_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    core, identity_store = make_core(tmp_path)
    socket = Socket()
    bind_session(core, socket)
    request = make_request(identity_store, **{field: value})
    with pytest.raises(RuntimeError, match="binding mismatch"):
        await core._handle_approval_request(socket, request)
    assert core.view_state.pending_approval_count == 0
    assert socket.sent == []


def test_wrong_nonce_or_digest_decision_and_duplicate_decision_are_rejected(
    tmp_path: Path,
) -> None:
    _, identity_store = make_core(tmp_path)
    store = ApprovalStore(tmp_path / "agent.db")
    request = make_request(identity_store)
    store.record_request(request)
    base = {
        "session_id": request.session_id,
        "device_id": request.device_id,
        "approval_request_id": request.approval_request_id,
        "approval_request_digest": request.approval_request_digest,
        "intent_id": request.intent_id,
        "consent_id": request.consent_id,
        "intent_digest": request.intent_digest,
        "challenge_nonce": request.challenge_nonce,
        "decision": "deny",
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "device_identity_generation": request.device_identity_generation,
        "device_key_thumbprint": request.device_key_thumbprint,
        "device_session_proof": "s" * 86,
    }
    for updates in (
        {"challenge_nonce": "x" * 43},
        {"intent_digest": "sha256:" + "d" * 64},
    ):
        decision = ApprovalDecisionMessage(**(base | updates))
        with pytest.raises(ApprovalConflict, match="binding mismatch"):
            store.record_decision(decision)

    exact = ApprovalDecisionMessage(**base)
    stored, duplicate = store.record_decision(exact)
    assert stored.status == "denied"
    assert duplicate is False
    _, duplicate = store.record_decision(exact)
    assert duplicate is True
    conflicting = exact.model_copy(update={"decision": "approve"})
    with pytest.raises(ApprovalConflict, match="no longer pending"):
        store.record_decision(conflicting)


@pytest.mark.asyncio
async def test_restart_requires_gateway_reissue_and_restart_after_decision_is_terminal(
    tmp_path: Path,
) -> None:
    core1, identity_store = make_core(tmp_path)
    socket1 = Socket()
    bind_session(core1, socket1)
    request = make_request(identity_store)
    await core1._handle_approval_request(socket1, request)
    assert core1.view_state.pending_approvals[0].actionable
    core1.ledger.close()

    core2, _ = make_core(tmp_path)
    assert core2.view_state.pending_approval_count == 1
    assert core2.view_state.pending_approvals[0].actionable is False
    socket2 = Socket()
    bind_session(core2, socket2)
    reissued = make_request(
        identity_store,
        message_id="approval-message-2",
        session_id="session-current",
        approval_request_id="approval-request-2",
        challenge_nonce="r" * 43,
    )
    await core2._handle_approval_request(socket2, reissued)
    assert core2.view_state.pending_approval_count == 1
    assert core2.view_state.pending_approvals[0].approval_request_id == (
        "approval-request-2"
    )
    await core2.submit_approval_decision("approval-request-2", "deny")
    core2.ledger.close()

    core3, _ = make_core(tmp_path)
    assert core3.view_state.pending_approval_count == 0


@pytest.mark.asyncio
async def test_hard_pause_invalidates_pending_but_preserves_started_unknown_truth(
    tmp_path: Path,
) -> None:
    core, identity_store = make_core(tmp_path)
    socket = Socket()
    bind_session(core, socket)
    await core._handle_approval_request(socket, make_request(identity_store))
    core._publish(
        active_job_id="job-started",
        current_task="Đang tạo đối tượng trong bản vẽ",
        outcome_unknown=True,
        runtime_state=RuntimeState.OUTCOME_UNKNOWN,
    )

    core.set_paused(True)

    assert core.view_state.pending_approval_count == 0
    assert core.view_state.hard_pause is True
    assert core.view_state.active_job_id == "job-started"
    assert core.view_state.current_task == "Đang tạo đối tượng trong bản vẽ"
    assert core.view_state.outcome_unknown is True
    with pytest.raises(RuntimeError, match="paused_by_user"):
        await core.submit_approval_decision("approval-request-1", "approve")
    assert socket.sent == []


@pytest.mark.asyncio
async def test_identity_generation_change_invalidates_pending(
    tmp_path: Path,
) -> None:
    core, identity_store = make_core(tmp_path)
    socket = Socket()
    bind_session(core, socket)
    await core._handle_approval_request(socket, make_request(identity_store))

    metadata_path = identity_store.root / "device.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["generation"] = 2
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    core._publish_approvals()

    assert core.view_state.pending_approval_count == 0
    assert core.approval_store.get("approval-request-1").status == "invalidated"


@pytest.mark.asyncio
async def test_capability_is_advertised_only_with_enabled_stable_identity(
    tmp_path: Path,
) -> None:
    core, _ = make_core(tmp_path / "enabled", enabled=True)
    socket = SessionSocket()
    await core._run_session(socket, "token")
    hello = json.loads(socket.sent[0])
    assert "cad.approval.device_local/1" in hello["capabilities"]

    disabled, _ = make_core(tmp_path / "disabled", enabled=False)
    disabled_socket = SessionSocket()
    await disabled._run_session(disabled_socket, "token")
    disabled_hello = json.loads(disabled_socket.sent[0])
    assert "cad.approval.device_local/1" not in disabled_hello["capabilities"]


def test_ui_has_typed_approval_only_and_host_command_has_no_approval_boolean() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "autocad_desktop_agent"
        / "ui"
        / "window.py"
    ).read_text(encoding="utf-8")
    assert "approve_approval(" in source
    assert "deny_approval(" in source
    assert ".executor" not in source
    assert "Named Pipe" not in source
    assert "COM" not in source

    assert "approval" not in ProgramCommandMessage.model_fields
    assert "confirm" not in ProgramCommandMessage.model_fields
