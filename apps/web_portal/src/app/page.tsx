import Link from "next/link";
import { getSession } from "@/lib/session";

export default async function HomePage() {
  const session = await getSession();

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <section className="card space-y-4 bg-slate-900 text-white border-none shadow-xl p-8 rounded-xl">
        <p className="text-xs font-bold uppercase tracking-wider text-blue-400">AutoCAD AI Connector MVP</p>
        <h1 className="text-3xl font-extrabold tracking-tight">Cổng thông tin & Quản lý Web Portal</h1>
        <p className="max-w-3xl text-slate-300 text-base leading-relaxed">
          Quản lý kết nối thiết bị, theo dõi bản vẽ AutoCAD, xem cấu trúc Scene Graph hình học và kết quả kiểm tra lỗi (Cleanup Audit) an toàn qua giao tiếp ChatGPT Web.
        </p>
        {session ? (
          <div className="flex flex-wrap gap-3 pt-2">
            <Link className="button primary bg-blue-600 hover:bg-blue-500 text-white border-none px-5 py-2.5 rounded-lg font-semibold" href="/devices">
              📱 Quản lý Thiết bị
            </Link>
            <Link className="button secondary bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-700 px-5 py-2.5 rounded-lg font-semibold" href="/scenes">
              📐 Drawing Scenes (Graph)
            </Link>
            <Link className="button secondary bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-700 px-5 py-2.5 rounded-lg font-semibold" href="/workflows">
              ⚙️ Lịch sử Workflow Runs
            </Link>
          </div>
        ) : (
          <div className="pt-2">
            <Link className="button primary bg-blue-600 hover:bg-blue-500 text-white border-none px-6 py-3 rounded-lg font-semibold" href="/login">
              🔐 Đăng nhập Auth0
            </Link>
          </div>
        )}
      </section>

      {/* Onboarding Checklist for Non-Technical Users */}
      <section className="card space-y-5 p-6 rounded-xl border border-slate-200 bg-white shadow-sm">
        <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          📋 Hướng dẫn cài đặt & Onboarding MVP (4 bước)
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
            <div className="flex items-center gap-2 text-blue-600 font-bold">
              <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center">1</span>
              Tải & Cài đặt Desktop Agent MVP
            </div>
            <p className="text-sm text-slate-600">
              Tải bộ cài đặt Windows (Standalone Installer), chạy file mà không cần cài Python hoặc Node.js.
            </p>
            <div className="pt-1">
              <a href="/dist/AutoCAD-AI-Connector-MVP.zip" className="text-xs font-semibold text-blue-600 underline hover:text-blue-800">
                ⬇️ Tải gói Desktop Agent MVP (.zip / installer)
              </a>
            </div>
          </div>

          <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
            <div className="flex items-center gap-2 text-blue-600 font-bold">
              <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center">2</span>
              Liên kết Thiết bị (Browser Pairing)
            </div>
            <p className="text-sm text-slate-600">
              Mở Desktop Agent, chọn "Liên kết tài khoản". Hệ thống tự động kích hoạt thiết bị của bạn.
            </p>
            <div className="pt-1">
              <Link href="/pair" className="text-xs font-semibold text-blue-600 underline hover:text-blue-800">
                🔗 Mở trang Browser Pairing
              </Link>
            </div>
          </div>

          <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
            <div className="flex items-center gap-2 text-blue-600 font-bold">
              <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center">3</span>
              Mở AutoCAD & Bản vẽ (Drawing A, B, C)
            </div>
            <p className="text-sm text-slate-600">
              Khởi động AutoCAD Mechanical 2025/2026. Managed Host R25 sẽ tự động nhận diện và sẵn sàng.
            </p>
          </div>

          <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
            <div className="flex items-center gap-2 text-blue-600 font-bold">
              <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs flex items-center justify-center">4</span>
              Kết nối ChatGPT & Phân tích Bản vẽ
            </div>
            <p className="text-sm text-slate-600">
              Thêm MCP Gateway URL vào ChatGPT Web và gửi Starter Prompt phân tích hình học bản vẽ.
            </p>
          </div>
        </div>
      </section>

      {/* Feature & Security Summary */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-5 rounded-xl bg-emerald-50 border border-emerald-200 space-y-2">
          <h3 className="font-bold text-emerald-900 flex items-center gap-2">
            🔒 Khóa Ghi Mặc Định (Write Lock)
          </h3>
          <p className="text-xs text-emerald-800 leading-relaxed">
            Mọi thao tác quan sát, tạo Scene Graph và kiểm tra lỗi (Audit) đều chạy ở chế độ chỉ đọc. Revision bản vẽ không bị thay đổi.
          </p>
        </div>

        <div className="p-5 rounded-xl bg-blue-50 border border-blue-200 space-y-2">
          <h3 className="font-bold text-blue-900 flex items-center gap-2">
            📐 Geometry & Scene Intelligence
          </h3>
          <p className="text-xs text-blue-800 leading-relaxed">
            Tự động nhận diện lỗ hình tròn, rãnh slot, đường tròn đồng tâm, lỗ lặp lại theo mảng và các đường chồng lấp/hở contour.
          </p>
        </div>

        <div className="p-5 rounded-xl bg-amber-50 border border-amber-200 space-y-2">
          <h3 className="font-bold text-amber-900 flex items-center gap-2">
            🛡️ Bằng chứng & Chẩn đoán an toàn
          </h3>
          <p className="text-xs text-amber-800 leading-relaxed">
            Dữ liệu chẩn đoán được mã hóa scrubbed (loại bỏ token/credential). Không lộ bí mật thiết bị hoặc nội dung nhạy cảm.
          </p>
        </div>
      </section>
    </div>
  );
}
