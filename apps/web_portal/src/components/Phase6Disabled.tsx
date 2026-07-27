import { Phase6Status } from "./Phase6Status";
import type { Phase6UiState } from "@/lib/env";

export function Phase6Disabled({ state }: { state: Phase6UiState }) {
  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">CAD Program 0.2</p>
        <h1 className="text-3xl font-bold">Phase 6 chưa khả dụng</h1>
      </div>
      <Phase6Status state={state} />
      <p className="card">
        Portal giữ fail-closed và không tải program, preview, job, receipt hoặc validation
        cho đến khi cấu hình phát hành được bật rõ ràng.
      </p>
    </section>
  );
}
