import { ConsentApproval } from "@/components/ConsentApproval";
import { Phase7Disabled } from "@/components/Phase7Disabled";
import { GatewayClient } from "@/lib/gateway-client";
import { phase7NotFound, phase7PageContext } from "@/lib/phase7-page";
import { recentAuthState } from "@/lib/security";

export default async function IntentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ error?: string; result?: string }>;
}) {
  const { id } = await params;
  const { session, state } = await phase7PageContext(`/intents/${id}`);
  if (!state.phase7Enabled) return <Phase7Disabled />;
  try {
    const client = new GatewayClient(session);
    const intent = await client.getIntent(id);
    if (!intent.consent_id) phase7NotFound(new Error("CONSENT_MISSING"));
    const consent = await client.getConsent(intent.consent_id);
    const query = await searchParams;
    return (
      <ConsentApproval
        intent={intent}
        consent={consent}
        csrfToken={session.csrfToken}
        recentAuth={recentAuthState(session)}
        approvalEnabled={state.recentAuthApprovalEnabled}
        error={query.error}
        result={query.result}
      />
    );
  } catch (error) {
    phase7NotFound(error);
  }
}
