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

  return json(response, 404, { error: "not_found" });
}).listen(4321, "127.0.0.1");
