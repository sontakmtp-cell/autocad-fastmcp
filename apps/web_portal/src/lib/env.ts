import "server-only";
import { z } from "zod";

const absoluteHttpUrl = z.string().url().refine((value) => {
  const url = new URL(value);
  return url.protocol === "https:"
    || (url.protocol === "http:"
      && ["127.0.0.1", "localhost", "::1"].includes(url.hostname));
}, "must use HTTPS, except for exact loopback development URLs");

const schema = z.object({
  PORTAL_PUBLIC_ORIGIN: absoluteHttpUrl,
  PORTAL_GATEWAY_BASE_URL: absoluteHttpUrl,
  PORTAL_SESSION_SECRET: z.string().min(32),
  PORTAL_OIDC_ISSUER: absoluteHttpUrl,
  PORTAL_OIDC_CLIENT_ID: z.string().min(1),
  PORTAL_OIDC_CLIENT_SECRET: z.string().optional(),
  PORTAL_OIDC_AUDIENCE: z.string().min(1),
  PORTAL_OIDC_SCOPES: z.string().default(
    "openid profile email autocad.read autocad.device.manage",
  ),
});

export type PortalEnv = z.infer<typeof schema>;

export function portalEnv(): PortalEnv {
  return schema.parse(process.env);
}
