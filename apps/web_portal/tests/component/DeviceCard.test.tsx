import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeviceCard } from "@/components/DeviceCard";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

describe("DeviceCard", () => {
  it("explains LT compatibility as a valid runtime role", () => {
    render(<DeviceCard device={{
      id: "device-a-0001",
      name: "Máy LT",
      is_default: false,
      connected: true,
      last_seen_at: "2026-07-25T12:00:00.000Z",
      runtime: {
        label: "AutoLISP/File IPC",
        role: "compatibility",
        health: "ready",
      },
    }} />);

    expect(screen.getByText("Máy LT")).toBeInTheDocument();
    expect(screen.getByText(/Tương thích LT/)).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/devices/device-a-0001");
  });
});
