import { redirect } from "next/navigation";
import { createOAuthTransaction } from "@/lib/oauth";
import { getSession } from "@/lib/session";

export default async function ReauthPage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string }>;
}) {
  if (!(await getSession())) {
    redirect("/login");
  }
  const requested = (await searchParams).returnTo ?? "/devices";
  const returnTo = createOAuthTransaction(requested, "recent_auth").returnTo;
  return (
    <section className="card mx-auto max-w-lg space-y-4">
      <h1 className="text-2xl font-bold">Xác thực lại để phê duyệt</h1>
      <p className="text-slate-600">
        Phiên đăng nhập hiện tại quá cũ hoặc không có bằng chứng `auth_time` từ nhà cung cấp đăng nhập.
        Portal sẽ yêu cầu Auth0 đăng nhập lại và quay về đúng yêu cầu này.
      </p>
      <a
        className="button primary"
        href={`/api/auth/login?recent=1&returnTo=${encodeURIComponent(returnTo)}`}
      >
        Tiếp tục xác thực lại
      </a>
    </section>
  );
}
