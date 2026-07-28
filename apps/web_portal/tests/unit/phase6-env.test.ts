import { afterEach, describe, expect, it, vi } from "vitest";
import { phase6UiState, phase7UiState } from "@/lib/env";

afterEach(() => vi.unstubAllEnvs());

describe("Phase 6 UI feature flags", () => {
  it("fails closed when all optional flags are absent", () => {
    vi.stubEnv("PORTAL_PHASE6_UI_ENABLED", "temporary");
    vi.stubEnv("PORTAL_MANAGED_WRITE_UI_ENABLED", "temporary");
    vi.stubEnv("PORTAL_MANAGED_WRITE_KILL_SWITCH", "temporary");
    delete process.env.PORTAL_PHASE6_UI_ENABLED;
    delete process.env.PORTAL_MANAGED_WRITE_UI_ENABLED;
    delete process.env.PORTAL_MANAGED_WRITE_KILL_SWITCH;
    expect(phase6UiState()).toEqual({
      phase6Enabled: false,
      managedWriteEnabled: false,
      killSwitchActive: true,
      gatewayStateAvailable: false,
    });
  });

  it("requires UI flag, write flag and a released kill switch", () => {
    vi.stubEnv("PORTAL_PHASE6_UI_ENABLED", "true");
    vi.stubEnv("PORTAL_MANAGED_WRITE_UI_ENABLED", "true");
    vi.stubEnv("PORTAL_MANAGED_WRITE_KILL_SWITCH", "true");
    const gatewayState = {
      program_v0_enabled: true,
      managed_write_enabled: true,
      kill_switch_active: false,
    };
    expect(phase6UiState(gatewayState).managedWriteEnabled).toBe(false);
    vi.stubEnv("PORTAL_MANAGED_WRITE_KILL_SWITCH", "false");
    expect(phase6UiState(gatewayState).managedWriteEnabled).toBe(true);
  });
});

describe("Phase 7 UI feature flags", () => {
  it("requires both presentation and recent-auth approval flags", () => {
    vi.stubEnv("PORTAL_PHASE7_UI_ENABLED", "false");
    vi.stubEnv("PORTAL_RECENT_AUTH_APPROVAL_ENABLED", "true");
    expect(phase7UiState()).toEqual({
      phase7Enabled: false,
      recentAuthApprovalEnabled: false,
    });
    vi.stubEnv("PORTAL_PHASE7_UI_ENABLED", "true");
    expect(phase7UiState()).toEqual({
      phase7Enabled: true,
      recentAuthApprovalEnabled: true,
    });
  });
});
