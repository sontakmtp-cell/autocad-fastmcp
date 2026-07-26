import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { exchangeCode } from "@/lib/oauth";
import {
  newCsrfToken,
  oauthCookieName,
  setSession,
  unsealOAuthTransaction,
} from "@/lib/session";
import { portalEnv } from "@/lib/env";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  const providerError = request.nextUrl.searchParams.get("error");
  const providerDescription = request.nextUrl.searchParams.get("error_description");
  const cookieStore = await cookies();
  const transactionValue = cookieStore.get(oauthCookieName())?.value;
  cookieStore.delete(oauthCookieName());
  const transaction = transactionValue ? await unsealOAuthTransaction(transactionValue) : null;

  if (!code || !state || !transaction || state !== transaction.state) {
    const safeProviderError = providerError && /^[a-z_]{1,64}$/.test(providerError)
      ? providerError
      : null;
    const reason = safeProviderError
      ? `provider_${safeProviderError}`
      : !code
        ? "callback_code_missing"
        : !state
          ? "callback_state_missing"
          : !transactionValue
            ? "transaction_cookie_missing"
            : !transaction
              ? "transaction_cookie_invalid"
              : "callback_state_mismatch";
    const safeDescription = providerDescription
      ?.replace(/[^A-Za-z0-9 .,:_()/-]/g, "")
      .slice(0, 160);
    console.warn(
      `Portal OAuth callback rejected: ${reason}`
      + (safeDescription ? ` (${safeDescription})` : ""),
    );
    return NextResponse.redirect(new URL("/login?error=oauth", portalEnv().PORTAL_PUBLIC_ORIGIN));
  }

  const identity = await exchangeCode(code, transaction);
  await setSession({ ...identity, csrfToken: newCsrfToken() });
  return NextResponse.redirect(new URL(transaction.returnTo, portalEnv().PORTAL_PUBLIC_ORIGIN));
}
