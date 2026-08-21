from autocad_gateway.application.job_service import DurableJobService
from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.preview_image_compat import (
    _observe_with_preview,
    _read_preview_artifact,
    _safe_agent_error_with_preview,
    _validate_c1_with_preview,
)
from autocad_gateway.preview_tool_guidance import _bind_preview_runtime


def test_preview_runtime_binds_exact_service_instance() -> None:
    services = DurableGatewayServices.__new__(DurableGatewayServices)

    _bind_preview_runtime(services)

    assert services.observe.__self__ is services
    assert services.observe.__func__ is _observe_with_preview
    assert services.read_artifact.__self__ is services
    assert services.read_artifact.__func__ is _read_preview_artifact
    assert DurableJobService._validate_c1_observation is _validate_c1_with_preview
    assert DurableJobService._safe_agent_error is _safe_agent_error_with_preview
