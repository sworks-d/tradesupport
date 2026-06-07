import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "INVESTIGELION — WILLE 司令室",
  description: "WILLE / KATSURAGI / AKAGI + DS 4 機 による投資判断システム",
};

// new_dashboard.html の <head> フォント読込を逐語再現。
// CSS が 'Inter' / 'Noto Sans JP' / 'Noto Serif JP' / 'JetBrains Mono' を
// 実名で参照しているため、next/font ではなく元と同一の Google Fonts <link> を使う。
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;500;600;700&family=Noto+Serif+JP:wght@500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
