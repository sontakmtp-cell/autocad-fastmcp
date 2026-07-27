import { Phase6Status } from "@/components/Phase6Status";
import { phase6PageContext } from "@/lib/phase6-page";

export default async function ProgramsPage() {
  const { state } = await phase6PageContext("/programs");
  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">CAD Program 0.2</p>
        <h1 className="text-3xl font-bold">Chương trình CAD và preview</h1>
        <p className="mt-2 max-w-3xl text-slate-600">
          Portal hiển thị bản ghi owner-scoped do Gateway tạo. Preview chỉ là giao dịch thử đã
          abort, không phải phê duyệt và chưa sửa bản vẽ.
        </p>
      </div>
      <Phase6Status state={state} />
      <div className="card space-y-2">
        <h2 className="text-xl font-bold">Mở từ activity hoặc ChatGPT</h2>
        <p>
          Dùng liên kết program, preview, job, receipt hoặc validation do kết quả CAD Program trả
          về. Portal không tự tạo program và không có nút phê duyệt trong Phase 6.
        </p>
      </div>
    </section>
  );
}
