import Link from "next/link";

export default function PairingNotFound() {
  return (
    <section className="card space-y-3">
      <h1 className="text-2xl font-bold">Yêu cầu không còn hiệu lực</h1>
      <p>Yêu cầu đã hết hạn, không tồn tại hoặc thuộc tài khoản khác.</p>
      <Link href="/pair">Quay lại hướng dẫn liên kết</Link>
    </section>
  );
}
