from __future__ import annotations

import sys

import pytest

from autocad_desktop_agent.credentials import DpapiCredentialProvider


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_dpapi_round_trip_does_not_store_plaintext(tmp_path):
    target = tmp_path / "device.credential"
    provider = DpapiCredentialProvider(target)
    credential = "phase4-dpapi-smoke-secret"
    provider.save(credential)
    assert provider.load() == credential
    assert credential.encode("utf-8") not in target.read_bytes()
