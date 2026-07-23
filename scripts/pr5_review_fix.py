from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: Path, marker: str, addition: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")


def patch_core() -> None:
    path = ROOT / "apps/desktop_agent/src/autocad_desktop_agent/core.py"

    replace_once(
        path,
        """        self._stop = asyncio.Event()\n        self._retry = asyncio.Event()\n        self._observers: list[Callable[[AgentViewState], None]] = []\n""",
        """        self._stop = asyncio.Event()\n        self._retry = asyncio.Event()\n        self._loop: asyncio.AbstractEventLoop | None = None\n        self._observers: list[Callable[[AgentViewState], None]] = []\n""",
    )
    replace_once(
        path,
        """        if intent == AgentIntent.RETRY:\n            self._retry.set()\n""",
        """        if intent == AgentIntent.RETRY:\n            self._set_event(self._retry)\n""",
    )
    replace_once(
        path,
        """        elif intent == AgentIntent.EXIT:\n            self._stop.set()\n\n    def set_paused(self, paused: bool) -> None:\n""",
        """        elif intent == AgentIntent.EXIT:\n            self._set_event(self._stop)\n            self._set_event(self._retry)\n\n    def _set_event(self, event: asyncio.Event) -> None:\n        loop = self._loop\n        if loop is not None and loop.is_running():\n            try:\n                loop.call_soon_threadsafe(event.set)\n                return\n            except RuntimeError:\n                pass\n        event.set()\n\n    def set_paused(self, paused: bool) -> None:\n""",
    )
    replace_once(
        path,
        """        if not paused:\n            self._retry.set()\n\n    async def run_forever(self) -> None:\n        import websockets\n\n        backoff = 1\n""",
        """        if not paused:\n            self._set_event(self._retry)\n\n    async def run_forever(self) -> None:\n        import websockets\n\n        self._loop = asyncio.get_running_loop()\n        backoff = 1\n""",
    )
    replace_once(
        path,
        """            await self._wait_for_retry(backoff)\n            backoff = min(backoff * 2, self.config.reconnect_max_seconds)\n        self.ledger.close()\n""",
        """            await self._wait_for_retry(backoff)\n            backoff = min(backoff * 2, self.config.reconnect_max_seconds)\n        self._loop = None\n        self.ledger.close()\n""",
    )
    replace_once(
        path,
        """    async def _handle_cancel(self, websocket: Any, message: CancelMessage) -> None:\n        entry = self.ledger.request_cancel(message.command_id)\n        if entry is None or entry.state in TERMINAL:\n            return\n""",
        """    async def _handle_cancel(self, websocket: Any, message: CancelMessage) -> None:\n        if message.session_id != self._session_id or message.device_id != self.config.device_id:\n            raise RuntimeError(\"cancel binding mismatch\")\n        entry = self.ledger.get(message.command_id)\n        if entry is None:\n            return\n        if entry.job_id != message.job_id or entry.device_id != message.device_id:\n            raise RuntimeError(\"cancel ledger binding mismatch\")\n        entry = self.ledger.request_cancel(message.command_id)\n        if entry is None or entry.state in TERMINAL:\n            return\n""",
    )


def patch_file_ipc() -> None:
    path = ROOT / "src/autocad_mcp/backends/file_ipc.py"
    replace_once(
        path,
        """IDLE_WAIT_TIMEOUT = min(2.0, max(0.25, TIMEOUT / 4.0))\nPING_RETRY_LIMIT = 1\n""",
        """IDLE_WAIT_TIMEOUT = min(2.0, max(0.25, TIMEOUT / 4.0))\nRESULT_SETTLE_TIMEOUT = 0.5\nPING_RETRY_LIMIT = 1\n""",
    )
    replace_once(
        path,
        """                if result_file.exists():\n                    current = self._inspect_runtime()\n                    transition_deadline = min(deadline, time.monotonic() + 0.5)\n                    while (\n                        current.error_code == \"no_active_document\"\n                        and time.monotonic() < transition_deadline\n                    ):\n                        await asyncio.sleep(POLL_INTERVAL)\n                        current = self._inspect_runtime()\n                    if current.error_code:\n                        return _error(current.error_code, current.error or current.error_code)\n                    if current.modal_dialog_active:\n                        return _error(\n                            \"modal_dialog_active\",\n                            \"AutoCAD entered a modal dialog while waiting for IPC.\",\n                        )\n                    if current.snapshot and current.snapshot.identity != expected_document.identity:\n                        return _error(\n                            \"active_document_changed\",\n                            \"The active AutoCAD document changed while the request was running.\",\n                            previous_document=expected_document.name,\n                            active_document=current.snapshot.name,\n                        )\n                    if current.idle is False:\n                        return _error(\n                            \"autocad_busy\",\n                            \"AutoCAD is busy after command routing.\",\n                            cmdactive=current.cmdactive,\n                        )\n                    parsed = self._read_result(result_file)\n                    if isinstance(parsed, CommandResult):\n                        return parsed\n                    if parsed.get(\"request_id\") != request_id or parsed.get(\"session_id\") != self._session_id:\n                        return _error(\n                            \"ipc_result_invalid\",\n                            \"IPC result identifiers do not match the request.\",\n                            expected_request_id=request_id,\n                            actual_request_id=parsed.get(\"request_id\"),\n                        )\n                    self._last_document = expected_document\n""",
        """                if result_file.exists():\n                    parsed = self._read_result(result_file)\n                    if isinstance(parsed, CommandResult):\n                        return parsed\n                    if parsed.get(\"request_id\") != request_id or parsed.get(\"session_id\") != self._session_id:\n                        return _error(\n                            \"ipc_result_invalid\",\n                            \"IPC result identifiers do not match the request.\",\n                            expected_request_id=request_id,\n                            actual_request_id=parsed.get(\"request_id\"),\n                        )\n\n                    settle_deadline = time.monotonic() + RESULT_SETTLE_TIMEOUT\n                    while True:\n                        current = self._inspect_runtime()\n                        if current.error_code == \"no_active_document\" and time.monotonic() < settle_deadline:\n                            await asyncio.sleep(POLL_INTERVAL)\n                            continue\n                        if current.error_code:\n                            return _error(current.error_code, current.error or current.error_code)\n                        if current.modal_dialog_active:\n                            return _error(\n                                \"modal_dialog_active\",\n                                \"AutoCAD entered a modal dialog while waiting for IPC.\",\n                            )\n                        if current.snapshot and current.snapshot.identity != expected_document.identity:\n                            return _error(\n                                \"active_document_changed\",\n                                \"The active AutoCAD document changed while the request was running.\",\n                                previous_document=expected_document.name,\n                                active_document=current.snapshot.name,\n                            )\n                        if current.idle is not False:\n                            break\n                        if time.monotonic() >= settle_deadline:\n                            return _error(\n                                \"autocad_busy\",\n                                \"AutoCAD is busy after command routing.\",\n                                cmdactive=current.cmdactive,\n                            )\n                        await asyncio.sleep(POLL_INTERVAL)\n\n                    self._last_document = expected_document\n""",
    )


def patch_tests() -> None:
    core_tests = ROOT / "apps/desktop_agent/tests/test_core.py"
    replace_once(
        core_tests,
        """import hashlib\nimport json\nimport sqlite3\n""",
        """import asyncio\nimport hashlib\nimport json\nimport sqlite3\nimport threading\nimport time\n""",
    )
    replace_once(
        core_tests,
        """from autocad_contracts import (\n    CommandMessage,\n""",
        """from autocad_contracts import (\n    CancelMessage,\n    CommandMessage,\n""",
    )
    append_once(
        core_tests,
        "test_ui_exit_wakes_offline_runner_from_another_thread",
        r'''
def test_ui_exit_wakes_offline_runner_from_another_thread(tmp_path, monkeypatch):
    core, _ = make_core(tmp_path)
    errors = []

    def fail_connect(*args, **kwargs):
        raise OSError("offline")

    import websockets

    monkeypatch.setattr(websockets, "connect", fail_connect)

    def runner():
        try:
            asyncio.run(core.run_forever())
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    thread = threading.Thread(target=runner)
    thread.start()
    deadline = time.monotonic() + 1
    while core._loop is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert core._loop is not None
    core.handle_intent(AgentIntent.EXIT)
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert errors == []


@pytest.mark.parametrize(
    ("intent", "event_name", "start_paused"),
    [
        (AgentIntent.RETRY, "_retry", False),
        (AgentIntent.RESUME, "_retry", True),
        (AgentIntent.EXIT, "_stop", False),
    ],
)
def test_ui_intents_signal_asyncio_events_across_threads(
    tmp_path, intent, event_name, start_paused
):
    core, _ = make_core(tmp_path)
    if start_paused:
        core.set_paused(True)
    ready = threading.Event()
    finished = threading.Event()
    errors = []

    def runner():
        async def wait_for_signal():
            core._loop = asyncio.get_running_loop()
            ready.set()
            await asyncio.wait_for(getattr(core, event_name).wait(), timeout=1)

        try:
            asyncio.run(wait_for_signal())
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=runner)
    thread.start()
    assert ready.wait(timeout=1)

    core.handle_intent(intent)

    assert finished.wait(timeout=1)
    thread.join(timeout=1)
    assert errors == []
    core._loop = None
    core.ledger.close()


def _record_pending_command(core, command):
    core.ledger.record_received(
        command_id=command.command_id,
        job_id=command.job_id,
        idempotency_key=command.idempotency_key,
        payload_hash=command.payload_hash,
        package=core.package,
        session_id=command.session_id,
        device_id=command.device_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"session_id": "wrong-session"},
        {"device_id": "wrong-device"},
        {"job_id": "wrong-job"},
    ],
)
async def test_cancel_binding_mismatch_fails_closed(tmp_path, updates):
    core, _ = make_core(tmp_path)
    command = make_command(core)
    _record_pending_command(core, command)
    cancel = CancelMessage(
        session_id=updates.get("session_id", command.session_id),
        device_id=updates.get("device_id", command.device_id),
        job_id=updates.get("job_id", command.job_id),
        command_id=command.command_id,
    )

    with pytest.raises(RuntimeError, match="cancel .*binding mismatch"):
        await core._handle_cancel(Socket(), cancel)

    entry = core.ledger.get(command.command_id)
    assert entry is not None
    assert entry.state == "received"
    assert entry.cancel_requested is False


@pytest.mark.asyncio
async def test_valid_cancel_uses_bound_ledger_entry(tmp_path):
    core, _ = make_core(tmp_path)
    command = make_command(core)
    _record_pending_command(core, command)
    socket = Socket()

    await core._handle_cancel(
        socket,
        CancelMessage(
            session_id=command.session_id,
            device_id=command.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
        ),
    )

    entry = core.ledger.get(command.command_id)
    assert entry is not None
    assert entry.state == "cancelled"
    assert entry.cancel_requested is True
    assert [message.message_type for message in socket.messages] == ["result"]
    assert socket.messages[0].status == "cancelled"
''',
    )

    ipc_tests = ROOT / "tests/test_file_ipc_reliability.py"
    append_once(
        ipc_tests,
        "test_completed_result_waits_for_dispatcher_to_settle",
        r'''
@pytest.mark.asyncio
async def test_completed_result_waits_for_dispatcher_to_settle(backend, monkeypatch):
    doc = FakeDocument()
    install_result_callback(backend, doc, {"document_name": "a.dwg"})
    states = iter(
        [
            runtime(doc),
            runtime(doc, idle=False, cmdactive=1),
            runtime(doc),
        ]
    )
    monkeypatch.setattr(backend, "_inspect_runtime", lambda: next(states))

    result = await backend.drawing_info()

    assert result.ok is True
    assert result.payload == {"document_name": "a.dwg"}
''',
    )


def main() -> None:
    patch_core()
    patch_file_ipc()
    patch_tests()
    (ROOT / "scripts/pr5_review_fix.py").unlink(missing_ok=True)
    (ROOT / ".github/workflows/pr5-review-fix.yml").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
