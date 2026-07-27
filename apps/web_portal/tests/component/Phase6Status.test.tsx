import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BindingSummary } from "@/components/BindingSummary";
import { Phase6Status } from "@/components/Phase6Status";
import { Phase6Warning } from "@/components/Phase6Warning";

const digest = `sha256:${"a".repeat(64)}`;

describe("Phase 6 presentation safety", () => {
  it("shows the fail-closed kill switch state in text", () => {
    render(<Phase6Status state={{
      phase6Enabled: true,
      managedWriteEnabled: false,
      killSwitchActive: true,
      gatewayStateAvailable: true,
    }} />);
    expect(screen.getByText("Write đang tắt")).toBeInTheDocument();
    expect(screen.getByText(/Kill switch đang hoạt động/)).toBeInTheDocument();
  });

  it("renders exact runtime, package and capability binding", () => {
    render(<BindingSummary binding={{
      runtime_id: "managed_dotnet_r25",
      runtime_role: "primary",
      host_family: "R25",
      host_version: "25.0",
      package_id: "managed-host",
      package_version: "0.2.0",
      package_hash: digest,
      capability_manifest_hash: digest,
      operation_registry_hash: digest,
      registry_version: "registry/0.2",
      policy_version: "phase6-policy/1",
    }} />);
    expect(screen.getByText("managed_dotnet_r25 · primary")).toBeInTheDocument();
    expect(screen.getByText("managed-host · 0.2.0")).toBeInTheDocument();
    expect(screen.getAllByText(digest)).toHaveLength(3);
  });

  it("uses safe copy for outcome unknown", () => {
    render(<Phase6Warning code="outcome_unknown" />);
    expect(screen.getByText(/không tự chạy lại/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
