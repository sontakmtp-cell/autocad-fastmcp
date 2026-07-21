from __future__ import annotations

import pytest

from autocad_gateway.app import GatewayConfig


def test_config_defaults_to_loopback_and_bounded_image_size():
    config = GatewayConfig()
    assert config.host == "127.0.0.1"
    assert config.max_image_bytes == 5 * 1024 * 1024


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.test"])
def test_config_rejects_non_loopback_host(host):
    with pytest.raises(ValueError):
        GatewayConfig(host=host).validate()

