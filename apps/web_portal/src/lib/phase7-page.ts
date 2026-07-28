import { notFound, redirect } from "next/navigation";
import { phase7UiState } from "./env";
import { GatewayError } from "./gateway-client";
import { getSession } from "./session";

export async function phase7PageContext(returnTo: string) {
  const session = await getSession();
  if (!session) {
    redirect(`/login?returnTo=${encodeURIComponent(returnTo)}`);
  }
  return { session, state: phase7UiState() };
}

export function phase7NotFound(error: unknown): never {
  if (
    (error instanceof GatewayError && error.status === 404)
    || error instanceof SyntaxError
    || (error instanceof Error && error.message === "CONSENT_MISSING")
    || (error instanceof Error && error.name === "ZodError")
  ) {
    notFound();
  }
  throw error;
}
