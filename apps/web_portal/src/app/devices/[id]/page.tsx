import { notFound, redirect } from "next/navigation";
import { RuntimeBadge } from "@/components/RuntimeBadge";
import { GatewayClient, GatewayError } from "@/lib/gateway-client";
import { getSession } from "@/lib/session";

export default async function DevicePage({ params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }

  let device;
  try {
    device = await new GatewayClient(session).getDevice((await params).id);
  } catch (error) {
    if (error instanceof GatewayError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  return (
    <section className="card space-y-5">
      <div>
        <h1 className="text-3xl font-bold">{device.name}</h1>
        <p className="mt-2">{device.connected ? "Đang kết nối" : "Ngoại tuyến"}</p>
      </div>
      <RuntimeBadge runtime={device.runtime} />
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <h2 className="font-bold text-red-800">Thu hồi thiết bị</h2>
        <p className="mt-2 text-sm text-red-900">
          Thiết bị sẽ mất quyền kết nối Gateway ngay. Desktop Agent và phiên Host cục bộ liên quan
          sẽ bị vô hiệu; việc này không gỡ cài đặt ứng dụng và không xóa bản vẽ.
        </p>
        <form className="mt-4" method="post" action={`/api/bff/devices/${device.id}/revoke`}>
          <input type="hidden" name="csrf" value={session.csrfToken} />
          <button className="danger" type="submit">Tôi hiểu, thu hồi thiết bị</button>
        </form>
      </div>
    </section>
  );
}
