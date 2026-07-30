import Link from "next/link";

export default function SceneNotFound() {
  return (
    <section className="card space-y-4">
      <h1 className="text-3xl font-bold">Không tìm thấy scene</h1>
      <p className="text-slate-600">
        Scene không tồn tại hoặc không thuộc tài khoản hiện tại.
      </p>
      <Link href="/scenes">← Drawing scenes</Link>
    </section>
  );
}
