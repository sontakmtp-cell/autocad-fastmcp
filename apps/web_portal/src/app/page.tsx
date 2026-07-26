import Link from "next/link";
import { getSession } from "@/lib/session";

export default async function HomePage() {
  const session = await getSession();

  return (
    <section className="card space-y-5">
      <p className="text-sm font-bold uppercase tracking-wide text-blue-700">Portal quản lý</p>
      <h1 className="text-3xl font-bold">Kết nối AutoCAD với tài khoản của bạn</h1>
      <p className="max-w-2xl text-slate-600">
        Portal chỉ quản lý tài khoản và thiết bị. Mọi thao tác AutoCAD vẫn chạy qua Desktop Agent
        trên máy của bạn.
      </p>
      {session ? (
        <div className="flex flex-wrap gap-3">
          <Link className="button primary" href="/devices">Xem thiết bị</Link>
          <Link className="button secondary" href="/pair">Liên kết thiết bị</Link>
        </div>
      ) : (
        <Link className="button primary" href="/login">Đăng nhập</Link>
      )}
    </section>
  );
}
