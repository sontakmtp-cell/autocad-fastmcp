import { notFound, redirect } from "next/navigation";
import { GatewayClient, GatewayError } from "@/lib/gateway-client";
import { getSession } from "@/lib/session";

export default async function PairPage({
  searchParams,
}: {
  searchParams: Promise<{ request?: string; result?: string }>;
}) {
  const query = await searchParams;
  const session = await getSession();
  if (!session) {
    const returnTo = query.request ? `/pair?request=${encodeURIComponent(query.request)}` : "/pair";
    redirect(`/login?returnTo=${encodeURIComponent(returnTo)}`);
  }

  if (query.result) {
    const message = query.result === "confirmed"
      ? "Đã liên kết thiết bị với tài khoản này."
      : "Đã từ chối yêu cầu liên kết. Thiết bị không được cấp quyền.";
    return <section className="card"><h1 className="text-2xl font-bold">{message}</h1></section>;
  }

  if (!query.request) {
    return (
      <section className="card space-y-3">
        <h1 className="text-3xl font-bold">Liên kết thiết bị</h1>
        <p>Mở Desktop Agent trên máy Windows rồi chọn “Liên kết thiết bị”.</p>
        <p className="text-slate-600">
          Agent sẽ mở một đường dẫn xác nhận dành riêng cho yêu cầu đó. Không nhập hoặc sao chép khóa bí mật vào đây.
        </p>
      </section>
    );
  }

  let pairing;
  try {
    pairing = await new GatewayClient(session).getPairing(query.request);
  } catch (error) {
    if (error instanceof GatewayError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  return (
    <section className="card space-y-5">
      <h1 className="text-3xl font-bold">Xác nhận liên kết</h1>
      <div className="rounded-lg bg-slate-50 p-4">
        <p className="text-sm text-slate-600">Thiết bị yêu cầu</p>
        <p className="mt-1 text-xl font-bold">{pairing.device_name}</p>
      </div>
      <p>
        Chỉ xác nhận nếu chính bạn vừa thao tác trên Desktop Agent. Sau khi xác nhận, thiết bị có
        thể kết nối Gateway dưới tài khoản này; nó không nhận token đăng nhập Portal.
      </p>
      <div className="flex flex-wrap gap-3">
        <form method="post" action={`/api/bff/pairings/${pairing.id}/confirm`}>
          <input type="hidden" name="csrf" value={session.csrfToken} />
          <button className="primary" type="submit">Xác nhận liên kết</button>
        </form>
        <form method="post" action={`/api/bff/pairings/${pairing.id}/deny`}>
          <input type="hidden" name="csrf" value={session.csrfToken} />
          <button className="secondary" type="submit">Từ chối</button>
        </form>
      </div>
    </section>
  );
}
