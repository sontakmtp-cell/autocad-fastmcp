import "server-only";
import { timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";
import { portalEnv } from "./env";
import type { PortalSession } from "./session";

function equal(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
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
  if (!contentType.startsWith("application/x-www-form-urlencoded")
    && !contentType.startsWith("multipart/form-data")) {
    throw new Error("CONTENT_TYPE_REJECTED");
  }

  const form = await request.formData();
  const csrf = form.get("csrf");
  if (typeof csrf !== "string" || !equal(csrf, session.csrfToken)) {
    throw new Error("CSRF_REJECTED");
  }
  return form;
}
