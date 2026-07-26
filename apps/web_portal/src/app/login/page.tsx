import { redirect } from "next/navigation";
import { getSession } from "@/lib/session";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string }>;
}) {
  if (await getSession()) {
    redirect("/devices");
  }
  const query = await searchParams;
  const loginHref = query.returnTo
    ? `/api/auth/login?returnTo=${encodeURIComponent(query.returnTo)}`
    : "/api/auth/login";

  return (
    <section className="card mx-auto max-w-lg space-y-4">
      <h1 className="text-2xl font-bold">Đăng nhập Portal</h1>
      <p className="text-slate-600">
        Trình duyệt chỉ nhận cookie phiên bảo mật. Token đăng nhập không được chuyển cho mã chạy trên trang.
      </p>
      {/* OAuth must use a full document navigation, not a Next.js/RSC fetch. */}
      <a className="button primary" href={loginHref}>Tiếp tục đăng nhập</a>
    </section>
  );
}
