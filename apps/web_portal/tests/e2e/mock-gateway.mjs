import { createServer } from "node:http";

const devices = {
  "owner-a-token": {
    id: "device-a-0001",
    name: "Máy của Owner A",
    is_default: true,
    connected: true,
    last_seen_at: "2026-07-25T12:00:00.000Z",
    runtime: { label: ".NET R25", role: "primary", health: "ready" },
  },
  "owner-b-token": {
    id: "device-b-0001",
    name: "Máy bí mật của Owner B",
    is_default: true,
    connected: true,
    last_seen_at: "2026-07-25T12:00:00.000Z",
    runtime: { label: "AutoLISP/File IPC", role: "compatibility", health: "ready" },
  },
};

const digest = (character) => `sha256:${character.repeat(64)}`;
const phase6 = {
  program: {
    program_id: "program-a-0001",
    program_revision: 1,
    device_id: "device-a-0001",
    document_id: "drawing33-document",
    source_snapshot_id: "snapshot-a-0001",
    expected_document_revision: "revision-before-001",
    schema_version: "cad.program/0.2",
    program_digest: digest("a"),
    risk_class: "low",
    missing_capabilities: [],
    pins: {
      runtime_id: "managed_dotnet_r25",
      runtime_role: "primary",
      host_family: "R25",
      host_version: "25.0",
      package_id: "autocad-mcp-managed-host",
      package_version: "0.2.0",
      package_hash: digest("b"),
      capability_manifest_hash: digest("c"),
      operation_registry_hash: digest("d"),
      registry_version: "cad.program.registry/0.2",
      policy_version: "phase6-policy/1",
    },
    created_at: "2026-07-27T08:00:00.000Z",
  },
  preview: {
    preview_id: "preview-a-0001",
    program_id: "program-a-0001",
    program_revision: 1,
    job_id: "job-preview-a-0001",
    program_digest: digest("a"),
    execution_digest: digest("e"),
    preview_digest: digest("f"),
    binding_digest: digest("1"),
    document_id: "drawing33-document",
    expected_document_revision: "revision-before-001",
    runtime_id: "managed_dotnet_r25",
    runtime_role: "primary",
    host_family: "R25",
    host_version: "25.0",
    package_id: "autocad-mcp-managed-host",
    package_version: "0.2.0",
    package_hash: digest("b"),
    capability_manifest_hash: digest("c"),
    operation_registry_hash: digest("d"),
    registry_version: "cad.program.registry/0.2",
    policy_version: "phase6-policy/1",
    planned_operation_count: 3,
    planned_entity_count: 2,
    planned_layer_count: 1,
    validation: { transaction_aborted: true, valid: true },
    expires_at: "2099-07-27T09:00:00.000Z",
    invalidated_reason: null,
    created_at: "2026-07-27T08:01:00.000Z",
  },
  receipt: {
    receipt_id: "receipt-a-0001",
    program_id: "program-a-0001",
    program_revision: 1,
    preview_id: "preview-a-0001",
    job_id: "job-commit-a-0001",
    program_digest: digest("a"),
    execution_digest: digest("5"),
    receipt_digest: digest("6"),
    preview_execution_digest: digest("e"),
    binding_digest: digest("1"),
    document_id: "drawing33-document",
    document_revision_before: "revision-before-001",
    document_revision_after: "revision-after-002",
    runtime_id: "managed_dotnet_r25",
    package_hash: digest("b"),
    capability_manifest_hash: digest("c"),
    operation_registry_hash: digest("d"),
    policy_version: "phase6-policy/1",
    effect_summary: { entities_created: 2, layers_created: 1 },
    durable_receipt: { duplicate: false, handles_recorded: 2 },
    created_at: "2026-07-27T08:02:00.000Z",
  },
  validation: {
    validation_id: "validation-a-0001",
    program_id: "program-a-0001",
    program_revision: 1,
    receipt_id: "receipt-a-0001",
    job_id: "job-validate-a-0001",
    execution_digest: digest("7"),
    binding_digest: digest("1"),
    document_revision: "revision-after-002",
    passed: true,
    report: { entity_count_match: true, bounds_match: true },
    created_at: "2026-07-27T08:03:00.000Z",
  },
  needsAttentionJob: {
    job_id: "job-unknown-a-0001",
    device_id: "device-a-0001",
    kind: "program_commit",
    effect_class: "write",
    state: "outcome_unknown",
    progress: { milestone: "started" },
    result: null,
    error_code: "outcome_unknown",
    created_at: "2026-07-27T08:02:00.000Z",
    updated_at: "2026-07-27T08:04:00.000Z",
  },
};

function json(response, status, value) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(value));
}

createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1:4321");
  if (
    request.method === "GET"
    && url.pathname === "/oidc/.well-known/openid-configuration"
  ) {
    return json(response, 200, {
      authorization_endpoint: "http://127.0.0.1:4321/authorize",
      token_endpoint: "http://127.0.0.1:4321/token",
      userinfo_endpoint: "http://127.0.0.1:4321/userinfo",
    });
  }
  if (request.method === "GET" && url.pathname === "/authorize") {
    const callback = new URL(url.searchParams.get("redirect_uri"));
    callback.searchParams.set("code", "playwright-code");
    callback.searchParams.set("state", url.searchParams.get("state"));
    response.writeHead(302, { location: callback.toString() });
    response.end();
    return;
  }
  if (request.method === "POST" && url.pathname === "/token") {
    return json(response, 200, {
      access_token: "owner-a-token",
      expires_in: 3600,
    });
  }
  if (request.method === "GET" && url.pathname === "/userinfo") {
    return json(response, 200, {
      sub: "owner-a",
      name: "Owner A",
    });
  }

  const token = (request.headers.authorization ?? "").replace(/^Bearer /, "");
  const ownedDevice = devices[token];
  if (!ownedDevice) {
    return json(response, 401, { error: "unauthorized" });
  }

  if (request.method === "GET" && url.pathname === "/api/portal/v1/devices") {
    return json(response, 200, { devices: [ownedDevice] });
  }
  if (
    request.method === "GET"
    && url.pathname === "/api/portal/v1/pairings/PAIRCODE1"
  ) {
    return json(response, 200, {
      id: "pairing-a-0001",
      device_name: "Device A - máy thật",
      requested_at: "2026-07-26T12:00:00.000Z",
      expires_at: "2026-07-26T12:10:00.000Z",
      status: "pending",
    });
  }

  const match = url.pathname.match(/^\/api\/portal\/v1\/devices\/([^/]+)$/);
  if (request.method === "GET" && match) {
    return match[1] === ownedDevice.id
      ? json(response, 200, ownedDevice)
      : json(response, 404, { error: "not_found" });
  }

  if (request.method === "GET" && url.pathname === "/api/portal/v1/phase6/status") {
    return token === "owner-a-token" || token === "owner-b-token"
      ? json(response, 200, {
        program_v0_enabled: true,
        managed_write_enabled: true,
        kill_switch_active: false,
      })
      : json(response, 401, { error: "unauthorized" });
  }

  if (
    request.method === "GET"
    && url.pathname === "/api/portal/v1/programs/program-a-0001/revisions/1"
  ) {
    return token === "owner-a-token"
      ? json(response, 200, phase6.program)
      : json(response, 404, { error: "not_found" });
  }
  if (request.method === "GET" && url.pathname === "/api/portal/v1/previews/preview-a-0001") {
    return token === "owner-a-token"
      ? json(response, 200, phase6.preview)
      : json(response, 404, { error: "not_found" });
  }
  if (request.method === "GET" && url.pathname === "/api/portal/v1/previews/preview-a-stale") {
    return token === "owner-a-token"
      ? json(response, 200, { ...phase6.preview, preview_id: "preview-a-stale", invalidated_reason: "runtime_changed" })
      : json(response, 404, { error: "not_found" });
  }
  if (request.method === "GET" && url.pathname === "/api/portal/v1/receipts/receipt-a-0001") {
    return token === "owner-a-token"
      ? json(response, 200, phase6.receipt)
      : json(response, 404, { error: "not_found" });
  }
  if (
    request.method === "GET"
    && url.pathname === "/api/portal/v1/validations/validation-a-0001"
  ) {
    return token === "owner-a-token"
      ? json(response, 200, phase6.validation)
      : json(response, 404, { error: "not_found" });
  }
  if (request.method === "GET" && url.pathname === "/api/portal/v1/jobs/job-unknown-a-0001") {
    return token === "owner-a-token"
      ? json(response, 200, phase6.needsAttentionJob)
      : json(response, 404, { error: "not_found" });
  }

  return json(response, 404, { error: "not_found" });
}).listen(4321, "127.0.0.1");
