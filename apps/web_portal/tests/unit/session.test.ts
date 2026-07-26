// @vitest-environment node

import { describe, expect, it } from "vitest";
import {
  sealOAuthTransaction,
  sealSession,
  unsealOAuthTransaction,
  unsealSession,
} from "@/lib/session";

describe("sealed Portal cookies", () => {
  it("round-trips valid session and OAuth state without exposing plaintext", async () => {
    const session = {
      subject: "owner-a",
      displayName: "Owner A",
      accessToken: "server-only-token",
      csrfToken: "csrf-token-at-least-thirty-two-characters",
      expiresAt: Math.floor(Date.now() / 1000) + 300,
    };
    const transaction = {
      state: "state-at-least-sixteen-characters",
      verifier: "pkce-verifier-at-least-thirty-two-characters",
      returnTo: "/devices",
    };

    const sealedSession = await sealSession(session);
    const sealedTransaction = await sealOAuthTransaction(transaction);

    expect(sealedSession).not.toContain(session.accessToken);
    expect(sealedTransaction).not.toContain(transaction.verifier);
    await expect(unsealSession(sealedSession)).resolves.toEqual(session);
    await expect(unsealOAuthTransaction(sealedTransaction)).resolves.toEqual(
      transaction,
    );
  });

  it("rejects tampered cookie values", async () => {
    const sealed = await sealOAuthTransaction({
      state: "state-at-least-sixteen-characters",
      verifier: "pkce-verifier-at-least-thirty-two-characters",
      returnTo: "/devices",
    });

    await expect(unsealOAuthTransaction(`${sealed}tampered`)).resolves.toBeNull();
  });
});
