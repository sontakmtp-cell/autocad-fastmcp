import "server-only";
import { createHash, timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";
import { portalEnv } from "./env";
import type { PortalSession } from "./session";

function equal(left: string, right: string): boolean {
  const a = createHash("sha256").update(left, "utf8").digest();
  const b = createHash("sha256").update(right, "utf8").digest();
  return timingSafeEqual(a, b);
}

export async function requireSafeMutation(
  request: NextRequest,
  session: PortalSession,
): Promise<FormData> {
  const expectedOrigin = new URL(portalEnv().PORTAL_PUBLIC_ORIGIN).origin;
  const origin = request.headers.get("origin");
  if (!origin || origin !== expectedOrigin) {
    throw new Error("ORIGIN_REJECTED");
  }

  const contentType = request.headers.get("content-type") ?? "";
  const mediaType = contentType.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType !== "application/x-www-form-urlencoded") {
    throw new Error("CONTENT_TYPE_REJECTED");
  }

  const form = await request.formData();
  const csrf = form.get("csrf");
  if (typeof csrf !== "string" || !equal(csrf, session.csrfToken)) {
    throw new Error("CSRF_REJECTED");
  }
  return form;
}

export type RecentAuthState = "valid" | "missing" | "stale" | "future";

export function recentAuthState(
  session: PortalSession,
  now = Math.floor(Date.now() / 1000),
): RecentAuthState {
  if (session.authenticatedAt === undefined) {
    return "missing";
  }
  if (session.authenticatedAt > now + 60) {
    return "future";
  }
  return now - session.authenticatedAt <= portalEnv().PORTAL_RECENT_AUTH_MAX_AGE_SECONDS
    ? "valid"
    : "stale";
}

export function requireRecentAuth(session: PortalSession): void {
  if (recentAuthState(session) !== "valid") {
    throw new Error("RECENT_AUTH_REQUIRED");
  }
}
