import Link from "next/link";

export default function DeviceNotFound() {
  return (
    <section className="card space-y-3">
      <h1 className="text-2xl font-bold">Không tìm thấy thiết bị</h1>
      <p>Thiết bị không tồn tại hoặc không thuộc tài khoản của bạn.</p>
      <Link href="/devices">Quay lại danh sách</Link>
    </section>
  );
}
