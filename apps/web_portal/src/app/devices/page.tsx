import { redirect } from "next/navigation";
import { DeviceCard } from "@/components/DeviceCard";
import { GatewayClient } from "@/lib/gateway-client";
import { getSession } from "@/lib/session";

export default async function DevicesPage() {
  const session = await getSession();
  if (!session) {
    redirect("/login?returnTo=/devices");
  }
  const devices = await new GatewayClient(session).listDevices();

  return (
    <section className="space-y-5">
      <div>
        <h1 className="text-3xl font-bold">Thiết bị của bạn</h1>
        <p className="mt-2 text-slate-600">
          Chỉ các thiết bị thuộc tài khoản đang đăng nhập được hiển thị.
        </p>
      </div>
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <p>
          Đang đăng nhập: <strong>{session.displayName}</strong>
        </p>
        <form method="post" action="/api/auth/logout">
          <input type="hidden" name="csrf" value={session.csrfToken} />
          <button className="secondary" type="submit">Đăng xuất / đổi tài khoản</button>
        </form>
      </div>
      {devices.length === 0 ? (
        <div className="card">Chưa có thiết bị. Mở Desktop Agent để bắt đầu liên kết.</div>
      ) : (
        <div className="grid gap-4">{devices.map((device) => <DeviceCard device={device} key={device.id} />)}</div>
      )}
    </section>
  );
}
