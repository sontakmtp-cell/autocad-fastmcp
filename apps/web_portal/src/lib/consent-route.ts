import "server-only";
import { NextRequest, NextResponse } from "next/server";
import { phase7UiState, portalEnv } from "./env";
import { GatewayClient, GatewayError } from "./gateway-client";
import { buildConsentDecision, type ConsentDecision } from "./phase7-approval";
import { requireRecentAuth, requireSafeMutation } from "./security";
import { requireSession } from "./session";

function redirectTo(path: string): NextResponse {
  return NextResponse.redirect(new URL(path, portalEnv().PORTAL_PUBLIC_ORIGIN), 303);
}

function reauthenticate(consentId: string): NextResponse {
  const returnTo = `/consents/${encodeURIComponent(consentId)}`;
  return redirectTo(`/reauth?returnTo=${encodeURIComponent(returnTo)}`);
}

export async function handleConsentDecision(
  request: NextRequest,
  consentId: string,
  decision: ConsentDecision,
): Promise<NextResponse> {
  let session;
  try {
    session = await requireSession();
  } catch {
    return new NextResponse("Cần đăng nhập", { status: 401 });
  }

  const state = phase7UiState();
  if (!state.phase7Enabled || !state.recentAuthApprovalEnabled) {
    return new NextResponse("Không tìm thấy", { status: 404 });
  }

  try {
    await requireSafeMutation(request, session);
  } catch (error) {
    const code = error instanceof Error ? error.message : "";
    const status = code === "CONTENT_TYPE_REJECTED" ? 415 : 403;
    return new NextResponse("Yêu cầu không hợp lệ", { status });
  }

  try {
    requireRecentAuth(session);
  } catch {
    return reauthenticate(consentId);
  }

  try {
    const client = new GatewayClient(session);
    const consent = await client.getConsent(consentId);
    const intent = await client.getIntent(consent.intent_id);
    const body = buildConsentDecision(session, intent, consent, decision);
    await client.decideConsent(consent.consent_id, decision, body);
    return redirectTo(`/intents/${intent.intent_id}?result=${decision === "approve" ? "approved" : "denied"}`);
  } catch (error) {
    if (error instanceof GatewayError && error.code === "recent_auth_required") {
      return reauthenticate(consentId);
    }
    if (
      error instanceof GatewayError
      && [409, 410, 412].includes(error.status)
    ) {
      const reason = error.status === 410 ? "expired" : "conflict";
      return redirectTo(`/consents/${encodeURIComponent(consentId)}?error=${reason}`);
    }
    if (
      (error instanceof GatewayError && error.status === 404)
      || (error instanceof Error && [
        "SESSION_RECORD_MISMATCH",
        "CHALLENGE_MISMATCH",
      ].includes(error.message))
    ) {
      return new NextResponse("Không tìm thấy", { status: 404 });
    }
    if (error instanceof Error && error.message === "APPROVAL_EXPIRED") {
      return redirectTo(`/consents/${encodeURIComponent(consentId)}?error=expired`);
    }
    if (error instanceof Error && [
      "CONSENT_NOT_PENDING",
      "ASSURANCE_MISMATCH",
    ].includes(error.message)) {
      return redirectTo(`/consents/${encodeURIComponent(consentId)}?error=conflict`);
    }
    return new NextResponse("Gateway tạm thời không khả dụng", { status: 502 });
  }
}
