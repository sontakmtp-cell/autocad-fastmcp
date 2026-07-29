import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutoCAD MCP Portal",
  description: "Liên kết và quản lý thiết bị AutoCAD MCP",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body>
        <header className="border-b border-slate-200 bg-white">
          <nav className="mx-auto flex max-w-4xl items-center justify-between p-4" aria-label="Chính">
            <Link className="font-bold text-slate-900 no-underline" href="/">
              AutoCAD MCP
            </Link>
            <div className="flex gap-4">
              <Link href="/pair">Liên kết</Link>
              <Link href="/devices">Thiết bị</Link>
              <Link href="/programs">CAD Program</Link>
              <Link href="/workflows">Workflows</Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-4xl p-4 py-10">{children}</main>
      </body>
    </html>
  );
}
