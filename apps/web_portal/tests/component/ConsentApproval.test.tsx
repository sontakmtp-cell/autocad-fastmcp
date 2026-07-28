import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ConsentApproval } from "@/components/ConsentApproval";
import type { Consent, ExecutionIntent } from "@/lib/contracts";

const digest = `sha256:${"a".repeat(64)}`;
const intent: ExecutionIntent = {
  schema_version: "cad.execution-intent/1",
  intent_id: "intent-a-0001",
  intent_version: 1,
  owner_subject: "owner-a",
  action: "program_commit",
  state: "awaiting_approval",
  state_version: 0,
  device_id: "device-a-0001",
  document_id: "drawing33-document",
  expected_document_revision: "revision-001",
  program_id: "program-a-0001",
  program_revision: 1,
  preview_id: "preview-a-0001",
  risk_class: "medium",
  required_assurance: "user_recent_auth",
  trusted_effect_summary: [{
    kind: "create_entities",
    count: 2,
    summary: "Tạo hai đối tượng từ preview",
  }],
  intent_digest: digest,
  created_at: "2026-07-27T08:00:00.000Z",
  expires_at: "2099-07-27T08:10:00.000Z",
  consent_id: "consent-a-0001",
  released_job_id: null,
};
const consent: Consent = {
  schema_version: "cad.consent/1",
  consent_id: "consent-a-0001",
  consent_version: 1,
  owner_subject: "owner-a",
  intent_id: "intent-a-0001",
  intent_version: 1,
  intent_digest: digest,
  required_assurance: "user_recent_auth",
  state: "requested",
  state_version: 0,
  challenge_nonce: "nonce-at-least-sixteen-characters",
  challenge_nonce_hash: digest,
  requested_at: "2026-07-27T08:00:00.000Z",
  expires_at: "2099-07-27T08:10:00.000Z",
  decided_at: null,
  decision_source: null,
  consumed_at: null,
};

afterEach(cleanup);

describe("ConsentApproval", () => {
  it("renders trusted Gateway facts and valid-session decision forms", () => {
    render(<ConsentApproval
      intent={intent}
      consent={consent}
      csrfToken="csrf-token-at-least-thirty-two-characters"
      recentAuth="valid"
      approvalEnabled
    />);
    expect(screen.getByText("Tạo hai đối tượng từ preview · 2")).toBeVisible();
    expect(screen.getByRole("button", { name: "Phê duyệt đúng yêu cầu này" })).toBeVisible();
    expect(screen.queryByText(/model says safe/i)).not.toBeInTheDocument();
  });

  it("offers safe reauthentication and no decision form when auth_time is stale", () => {
    render(<ConsentApproval
      intent={intent}
      consent={consent}
      csrfToken="csrf-token-at-least-thirty-two-characters"
      recentAuth="stale"
      approvalEnabled
    />);
    expect(screen.getByRole("link", { name: "Xác thực lại an toàn" })).toHaveAttribute(
      "href",
      expect.stringContaining("recent=1"),
    );
    expect(screen.queryByRole("button", { name: "Phê duyệt đúng yêu cầu này" }))
      .not.toBeInTheDocument();
  });

  it("has no bypass when the Phase 7 approval flag is disabled", () => {
    render(<ConsentApproval
      intent={intent}
      consent={consent}
      csrfToken="csrf-token-at-least-thirty-two-characters"
      recentAuth="valid"
      approvalEnabled={false}
    />);
    expect(screen.getByText(/Không có đường bỏ qua/)).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("does not render a path or secret even if a trusted summary is malformed", () => {
    render(<ConsentApproval
      intent={{
        ...intent,
        trusted_effect_summary: [{
          kind: "create_entities",
          count: 1,
          summary: "C:\\private\\drawing33.dwg access_token=secret",
        }],
      }}
      consent={consent}
      csrfToken="csrf-token-at-least-thirty-two-characters"
      recentAuth="valid"
      approvalEnabled
    />);
    expect(screen.getByText("Chi tiết tác động đã được ẩn vì bảo mật · 1")).toBeVisible();
    expect(screen.queryByText(/private.*drawing33/i)).not.toBeInTheDocument();
  });
});
