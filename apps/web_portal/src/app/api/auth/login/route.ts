import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { authorizationUrl, createOAuthTransaction } from "@/lib/oauth";
import { oauthCookieName, sealOAuthTransaction } from "@/lib/session";
import { portalEnv } from "@/lib/env";

export async function GET(request: NextRequest) {
  const transaction = createOAuthTransaction(request.nextUrl.searchParams.get("returnTo") ?? "/devices");
  (await cookies()).set(oauthCookieName(), await sealOAuthTransaction(transaction), {
    httpOnly: true,
    secure: portalEnv().PORTAL_PUBLIC_ORIGIN.startsWith("https://"),
    sameSite: "lax",
    // The production cookie uses the __Host- prefix, which requires Path=/.
    path: "/",
    maxAge: 600,
  });
  return NextResponse.redirect(await authorizationUrl(transaction));
}
