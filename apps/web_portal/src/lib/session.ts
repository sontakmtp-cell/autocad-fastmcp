import "server-only";
import { createHash, randomBytes } from "node:crypto";
import { cookies } from "next/headers";
import { EncryptJWT, jwtDecrypt } from "jose";
import { z } from "zod";
import { portalEnv } from "./env";

export function sessionCookieName(): string {
  return portalEnv().PORTAL_PUBLIC_ORIGIN.startsWith("https://")
    ? "__Host-autocad_portal"
    : "autocad_portal";
}

export function oauthCookieName(): string {
  return portalEnv().PORTAL_PUBLIC_ORIGIN.startsWith("https://")
    ? "__Host-autocad_oauth"
    : "autocad_oauth";
}

const sessionSchema = z.object({
  subject: z.string().min(1),
  displayName: z.string().min(1),
  accessToken: z.string().min(1),
  csrfToken: z.string().min(32),
  expiresAt: z.number().int().positive(),
});

const oauthTransactionSchema = z.object({
  state: z.string().min(16),
  verifier: z.string().min(32),
  returnTo: z.string().startsWith("/"),
});

export type PortalSession = z.infer<typeof sessionSchema>;
export type SealedOAuthTransaction = z.infer<typeof oauthTransactionSchema>;

function encryptionKey(): Uint8Array {
  return new Uint8Array(
    createHash("sha256").update(portalEnv().PORTAL_SESSION_SECRET, "utf8").digest(),
  );
}

export function newCsrfToken(): string {
  return randomBytes(32).toString("base64url");
}

export async function sealSession(session: PortalSession): Promise<string> {
  return new EncryptJWT(session)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(session.expiresAt)
    .encrypt(encryptionKey());
}

export async function unsealSession(value: string): Promise<PortalSession | null> {
  try {
    const { payload } = await jwtDecrypt(value, encryptionKey(), {
      clockTolerance: 5,
    });
    return sessionSchema.parse(payload);
  } catch {
    return null;
  }
}

export async function sealOAuthTransaction(transaction: SealedOAuthTransaction): Promise<string> {
  return new EncryptJWT(transaction)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime("10m")
    .encrypt(encryptionKey());
}

export async function unsealOAuthTransaction(
  value: string,
): Promise<SealedOAuthTransaction | null> {
  try {
    const { payload } = await jwtDecrypt(value, encryptionKey(), { clockTolerance: 5 });
    return oauthTransactionSchema.parse(payload);
  } catch {
    return null;
  }
}

export async function getSession(): Promise<PortalSession | null> {
  const value = (await cookies()).get(sessionCookieName())?.value;
  return value ? unsealSession(value) : null;
}

export async function requireSession(): Promise<PortalSession> {
  const session = await getSession();
  if (!session) {
    throw new Error("AUTH_REQUIRED");
  }
  return session;
}

export async function setSession(session: PortalSession): Promise<void> {
  (await cookies()).set(sessionCookieName(), await sealSession(session), {
    httpOnly: true,
    secure: portalEnv().PORTAL_PUBLIC_ORIGIN.startsWith("https://"),
    sameSite: "lax",
    path: "/",
    expires: new Date(session.expiresAt * 1000),
  });
}

export async function clearSession(): Promise<void> {
  (await cookies()).delete(sessionCookieName());
}
