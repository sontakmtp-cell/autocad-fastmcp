import "server-only";
import { createHash, randomBytes } from "node:crypto";
import { createLocalJWKSet, jwtVerify } from "jose";
import { z } from "zod";
import { portalEnv } from "./env";

const discoverySchema = z.object({
  authorization_endpoint: z.string().url(),
  token_endpoint: z.string().url(),
  userinfo_endpoint: z.string().url(),
  jwks_uri: z.string().url(),
});

const tokenSchema = z.object({
  access_token: z.string().min(1),
  id_token: z.string().min(1),
  expires_in: z.number().int().positive().default(3600),
});

const profileSchema = z.object({
  sub: z.string().min(1),
  name: z.string().optional(),
  email: z.string().optional(),
});

export type OAuthTransaction = {
  state: string;
  verifier: string;
  nonce: string;
  returnTo: string;
  purpose: "login" | "recent_auth";
};

function safeReturnPath(value: string): string {
  if (["/", "/devices", "/programs"].includes(value)) {
    return value;
  }
  if (/^\/(?:devices|previews|receipts|jobs|validations)\/[A-Za-z0-9_-]{8,128}$/.test(value)) {
    return value;
  }
  if (/^\/(?:intents|consents)\/[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(value)) {
    return value;
  }
  if (/^\/programs\/[A-Za-z0-9_-]{8,128}\/revisions\/[1-9][0-9]{0,9}$/.test(value)) {
    return value;
  }
  if (/^\/pair\?request=[A-Za-z0-9_-]{8,128}$/.test(value)) {
    return value;
  }
  return "/devices";
}

async function discovery() {
  const issuer = portalEnv().PORTAL_OIDC_ISSUER.replace(/\/+$/, "");
  const response = await fetch(`${issuer}/.well-known/openid-configuration`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("OIDC_DISCOVERY_FAILED");
  }
  return discoverySchema.parse(await response.json());
}

export function createOAuthTransaction(
  returnTo = "/devices",
  purpose: OAuthTransaction["purpose"] = "login",
): OAuthTransaction {
  return {
    state: randomBytes(24).toString("base64url"),
    verifier: randomBytes(48).toString("base64url"),
    nonce: randomBytes(24).toString("base64url"),
    returnTo: safeReturnPath(returnTo),
    purpose,
  };
}

export async function authorizationUrl(transaction: OAuthTransaction): Promise<URL> {
  const env = portalEnv();
  const endpoints = await discovery();
  const challenge = createHash("sha256").update(transaction.verifier).digest("base64url");
  const url = new URL(endpoints.authorization_endpoint);
  url.searchParams.set("client_id", env.PORTAL_OIDC_CLIENT_ID);
  url.searchParams.set("audience", env.PORTAL_OIDC_AUDIENCE);
  url.searchParams.set("redirect_uri", `${env.PORTAL_PUBLIC_ORIGIN}/api/auth/callback`);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", env.PORTAL_OIDC_SCOPES);
  // Portal identity and recent-auth must not silently reuse an unknown browser account.
  url.searchParams.set("prompt", "login");
  // max_age also requires the provider to return a verifiable auth_time claim.
  url.searchParams.set(
    "max_age",
    String(env.PORTAL_RECENT_AUTH_MAX_AGE_SECONDS),
  );
  url.searchParams.set("state", transaction.state);
  url.searchParams.set("nonce", transaction.nonce);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url;
}

export async function exchangeCode(code: string, transaction: OAuthTransaction) {
  const env = portalEnv();
  const endpoints = await discovery();
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: env.PORTAL_OIDC_CLIENT_ID,
    redirect_uri: `${env.PORTAL_PUBLIC_ORIGIN}/api/auth/callback`,
    code,
    code_verifier: transaction.verifier,
  });
  if (env.PORTAL_OIDC_CLIENT_SECRET) {
    body.set("client_secret", env.PORTAL_OIDC_CLIENT_SECRET);
  }

  const tokenResponse = await fetch(endpoints.token_endpoint, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
    cache: "no-store",
  });
  if (!tokenResponse.ok) {
    throw new Error("OIDC_TOKEN_EXCHANGE_FAILED");
  }
  const token = tokenSchema.parse(await tokenResponse.json());
  const jwksResponse = await fetch(endpoints.jwks_uri, {
    cache: "no-store",
  });
  if (!jwksResponse.ok) {
    throw new Error("OIDC_JWKS_FAILED");
  }
  const jwks = z.object({ keys: z.array(z.record(z.string(), z.unknown())).min(1).max(16) })
    .parse(await jwksResponse.json());
  const issuer = env.PORTAL_OIDC_ISSUER;
  const { payload } = await jwtVerify(token.id_token, createLocalJWKSet(jwks), {
    issuer,
    audience: env.PORTAL_OIDC_CLIENT_ID,
    algorithms: ["RS256"],
  });
  if (payload.nonce !== transaction.nonce) {
    throw new Error("OIDC_NONCE_MISMATCH");
  }
  const authenticatedAt = z.number().int().positive().parse(payload.auth_time);
  if (authenticatedAt > Math.floor(Date.now() / 1000) + 60) {
    throw new Error("OIDC_AUTH_TIME_INVALID");
  }
  const profileResponse = await fetch(endpoints.userinfo_endpoint, {
    headers: { authorization: `Bearer ${token.access_token}` },
    cache: "no-store",
  });
  if (!profileResponse.ok) {
    throw new Error("OIDC_PROFILE_FAILED");
  }
  const profile = profileSchema.parse(await profileResponse.json());
  if (payload.sub !== profile.sub) {
    throw new Error("OIDC_SUBJECT_MISMATCH");
  }
  return {
    subject: profile.sub,
    ownerKey: `user-${createHash("sha256")
      .update(`${issuer}\0${profile.sub}`, "utf8")
      .digest("hex")}`,
    displayName: profile.name ?? profile.email ?? "Người dùng",
    accessToken: token.access_token,
    expiresAt: Math.floor(Date.now() / 1000) + token.expires_in,
    authenticatedAt,
  };
}
