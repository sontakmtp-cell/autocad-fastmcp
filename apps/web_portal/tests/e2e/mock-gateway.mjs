import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { exportJWK, generateKeyPair, SignJWT } from "jose";

const { publicKey, privateKey } = await generateKeyPair("RS256");
const publicJwk = await exportJWK(publicKey);
publicJwk.kid = "playwright-key";
publicJwk.alg = "RS256";
let oidcNonce = "";

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
const ownerAKey = `user-${createHash("sha256")
  .update("http://127.0.0.1:4321/oidc\0owner-a")
  .digest("hex")}`;
const nonce = "phase7-challenge-nonce-owner-a-0001";
const nonceHash = `sha256:${createHash("sha256").update(nonce).digest("hex")}`;
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

function phase7Intent(intentId, consentId, overrides = {}) {
  return {
    schema_version: "cad.execution-intent/1",
    intent_id: intentId,
    intent_version: 1,
    owner_subject: ownerAKey,
    actor_principal: {
      issuer: "http://127.0.0.1:4321/oidc",
      subject: "owner-a",
    },
    action: "program_commit",
    state: "awaiting_approval",
    state_version: 0,
    device_id: "device-a-0001",
    device_identity_generation: 1,
    device_key_thumbprint: digest("2"),
    document_id: "drawing33-document",
    expected_document_revision: "revision-before-001",
    program_id: "program-a-0001",
    program_revision: 1,
    program_digest: digest("a"),
    preview_id: "preview-a-0001",
    preview_digest: digest("f"),
    preview_execution_digest: digest("e"),
    preview_expires_at: "2099-07-27T09:00:00.000Z",
    deterministic_receipt_id: "receipt-a-phase7",
    commit_execution_digest: digest("5"),
    runtime_pins: {
      runtime_id: "managed_dotnet_r25",
      runtime_role: "primary",
      host_family: "R25",
      host_version: "25.0",
      agent_package_id: "desktop-agent",
      agent_package_version: "0.2.0",
      agent_package_hash: digest("3"),
      host_package_id: "managed-host",
      host_package_version: "0.2.0",
      host_package_hash: digest("b"),
    },
    policy_pins: {
      capability_manifest_hash: digest("c"),
      operation_registry_hash: digest("d"),
      registry_version: "cad.program.registry:0.2",
      policy_version: "phase7-policy:1",
    },
    risk_class: "medium",
    required_assurance: "user_recent_auth",
    trusted_effect_summary: [{
      kind: "create_entities",
      count: 2,
      summary: "Tạo hai đối tượng theo preview đã khóa",
    }],
    intent_digest: digest(intentId.endsWith("2") ? "8" : "9"),
    idempotency_key: `phase7-${intentId}`,
    request_hash: digest("4"),
    created_at: "2026-07-27T08:00:00.000Z",
    expires_at: "2099-07-27T08:10:00.000Z",
    consent_id: consentId,
    released_job_id: null,
    ...overrides,
  };
}

function phase7Consent(consentId, intent, overrides = {}) {
  return {
    schema_version: "cad.consent/1",
    consent_id: consentId,
    consent_version: 1,
    owner_subject: ownerAKey,
    intent_id: intent.intent_id,
    intent_version: intent.intent_version,
    intent_digest: intent.intent_digest,
    required_assurance: intent.required_assurance,
    state: "requested",
    state_version: 0,
    challenge_nonce: nonce,
    challenge_nonce_hash: nonceHash,
    requested_at: "2026-07-27T08:00:00.000Z",
    expires_at: "2099-07-27T08:10:00.000Z",
    decided_at: null,
    decision_source: null,
    decision_principal: null,
    decision_device_id: null,
    decision_device_identity_generation: null,
    consumed_at: null,
    ...overrides,
  };
}

const intents = {
  "intent-a-0001": phase7Intent("intent-a-0001", "consent-a-0001"),
  "intent-a-expired": phase7Intent("intent-a-expired", "consent-a-expired"),
  "intent-a-replayed": phase7Intent("intent-a-replayed", "consent-a-replayed"),
  "intent-a-stale": phase7Intent("intent-a-stale", "consent-a-stale"),
  "intent-a-version": phase7Intent("intent-a-version", "consent-a-version"),
  "intent-a-deny": phase7Intent("intent-a-deny", "consent-a-deny"),
};
const consents = {
  "consent-a-0001": phase7Consent("consent-a-0001", intents["intent-a-0001"]),
  "consent-a-expired": phase7Consent(
    "consent-a-expired",
    intents["intent-a-expired"],
    { expires_at: "2020-01-01T00:00:00.000Z" },
  ),
  "consent-a-replayed": phase7Consent(
    "consent-a-replayed",
    intents["intent-a-replayed"],
    {
      state: "consumed",
      consent_version: 2,
      state_version: 2,
      decided_at: "2026-07-27T08:01:00.000Z",
      decision_source: "portal_recent_auth",
      consumed_at: "2026-07-27T08:02:00.000Z",
    },
  ),
  "consent-a-stale": phase7Consent(
    "consent-a-stale",
    intents["intent-a-stale"],
  ),
  "consent-a-version": phase7Consent(
    "consent-a-version",
    intents["intent-a-version"],
  ),
  "consent-a-deny": phase7Consent(
    "consent-a-deny",
    intents["intent-a-deny"],
  ),
};

function json(response, status, value) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(value));
}

async function readJson(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1:4321");
  if (
    request.method === "GET"
    && url.pathname === "/oidc/.well-known/openid-configuration"
  ) {
    return json(response, 200, {
      authorization_endpoint: "http://127.0.0.1:4321/authorize",
      token_endpoint: "http://127.0.0.1:4321/token",
      userinfo_endpoint: "http://127.0.0.1:4321/userinfo",
      jwks_uri: "http://127.0.0.1:4321/jwks",
    });
  }
  if (request.method === "GET" && url.pathname === "/jwks") {
    return json(response, 200, { keys: [publicJwk] });
  }
  if (request.method === "GET" && url.pathname === "/authorize") {
    oidcNonce = url.searchParams.get("nonce") ?? "";
    const callback = new URL(url.searchParams.get("redirect_uri"));
    callback.searchParams.set("code", "playwright-code");
    callback.searchParams.set("state", url.searchParams.get("state"));
    response.writeHead(302, { location: callback.toString() });
    response.end();
    return;
  }
  if (request.method === "POST" && url.pathname === "/token") {
    const now = Math.floor(Date.now() / 1000);
    const idToken = await new SignJWT({
      sub: "owner-a",
      name: "Owner A",
      nonce: oidcNonce,
      auth_time: now,
    })
      .setProtectedHeader({ alg: "RS256", kid: "playwright-key" })
      .setIssuer("http://127.0.0.1:4321/oidc")
      .setAudience("playwright-client")
      .setIssuedAt(now)
      .setExpirationTime(now + 3600)
      .sign(privateKey);
    return json(response, 200, {
      access_token: "owner-a-token",
      id_token: idToken,
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

  const intentMatch = url.pathname.match(/^\/api\/portal\/v1\/intents\/([^/]+)$/);
  if (request.method === "GET" && intentMatch) {
    const intent = intents[intentMatch[1]];
    return token === "owner-a-token" && intent
      ? json(response, 200, intent)
      : json(response, 404, { code: "not_found" });
  }
  const consentMatch = url.pathname.match(/^\/api\/portal\/v1\/consents\/([^/]+)$/);
  if (request.method === "GET" && consentMatch) {
    const consent = consents[consentMatch[1]];
    if (token !== "owner-a-token" || !consent) {
      return json(response, 404, { code: "not_found" });
    }
    const { challenge_nonce, ...record } = consent;
    return json(response, 200, {
      consent: record,
      intent: intents[consent.intent_id],
      decision_nonce: challenge_nonce,
    });
  }
  const decisionMatch = url.pathname.match(
    /^\/api\/portal\/v1\/consents\/([^/]+)\/(approve|deny)$/,
  );
  if (request.method === "POST" && decisionMatch) {
    const consent = consents[decisionMatch[1]];
    if (token !== "owner-a-token" || !consent) {
      return json(response, 404, { code: "not_found" });
    }
    if (consent.state !== "requested") {
      return json(response, 409, { code: "consent_version_conflict" });
    }
    if (consent.consent_id === "consent-a-version") {
      return json(response, 409, { code: "consent_version_conflict" });
    }
    if (Date.parse(consent.expires_at) <= Date.now()) {
      return json(response, 410, { code: "approval_expired" });
    }
    const body = await readJson(request);
    const expectedKeys = [
      "challenge_nonce",
      "consent_version",
      "decision",
      "intent_digest",
    ];
    if (
      JSON.stringify(Object.keys(body).sort()) !== JSON.stringify(expectedKeys)
      || body.intent_digest !== consent.intent_digest
      || body.consent_version !== consent.consent_version
      || body.challenge_nonce !== consent.challenge_nonce
      || body.decision !== decisionMatch[2]
    ) {
      return json(response, 400, { code: "invalid_decision_binding" });
    }
    consent.state = decisionMatch[2] === "approve" ? "approved" : "denied";
    consent.consent_version += 1;
    consent.state_version += 1;
    consent.decided_at = new Date().toISOString();
    consent.decision_source = "portal_recent_auth";
    return json(response, 200, {
      status: consent.state,
      consent_id: consent.consent_id,
      consent_version: consent.consent_version,
      intent_id: consent.intent_id,
    });
  }

  return json(response, 404, { error: "not_found" });
}).listen(4321, "127.0.0.1");
