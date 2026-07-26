import { NextRequest, NextResponse } from "next/server";
import { requireSession, sessionCookieName } from "@/lib/session";
import { requireSafeMutation } from "@/lib/security";
import { portalEnv } from "@/lib/env";

export async function POST(request: NextRequest) {
  try {
    const env = portalEnv();
    const session = await requireSession();
    await requireSafeMutation(request, session);
    const providerLogout = new URL(
      "/v2/logout",
      env.PORTAL_OIDC_ISSUER,
    );
    providerLogout.searchParams.set("client_id", env.PORTAL_OIDC_CLIENT_ID);
    providerLogout.searchParams.set("returnTo", `${env.PORTAL_PUBLIC_ORIGIN}/`);
    const response = NextResponse.redirect(providerLogout, 303);
    response.cookies.set(sessionCookieName(), "", {
      httpOnly: true,
      secure: env.PORTAL_PUBLIC_ORIGIN.startsWith("https://"),
      sameSite: "lax",
      path: "/",
      expires: new Date(0),
    });
    return response;
  } catch (error) {
    console.warn(
      `Portal logout rejected: ${
        error instanceof Error ? error.message : "UNKNOWN"
      }`,
    );
    return new NextResponse("Yêu cầu không hợp lệ", { status: 400 });
  }
}
