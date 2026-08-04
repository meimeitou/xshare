import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "XShare",
  description: "A股数据分析工具",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body
        className="min-h-[100dvh] flex flex-col"
        style={{ background: "var(--bg)", color: "var(--text)" }}
      >
        <Nav />
        <main className="flex-1 w-full max-w-[1400px] mx-auto px-4 pb-10 pt-7 md:px-6 md:pb-12 md:pt-8">
          {children}
        </main>
      </body>
    </html>
  );
}
