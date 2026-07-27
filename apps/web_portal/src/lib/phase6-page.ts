import { notFound, redirect } from "next/navigation";
import { GatewayClient, GatewayError } from "./gateway-client";
import { phase6UiState } from "./env";
import { getSession } from "./session";

export async function phase6PageContext(returnTo: string) {
  const session = await getSession();
  if (!session) {
    redirect(`/login?returnTo=${encodeURIComponent(returnTo)}`);
  }
  let gatewayState;
  try {
    gatewayState = await new GatewayClient(session).getPhase6ReleaseStatus();
  } catch {
    gatewayState = undefined;
  }
  return { session, state: phase6UiState(gatewayState) };
}

export function phase6NotFound(error: unknown): never {
  if (
    (error instanceof GatewayError && error.status === 404)
    || error instanceof SyntaxError
    || (error instanceof Error && error.name === "ZodError")
  ) {
    notFound();
  }
  throw error;
}
