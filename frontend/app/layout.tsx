import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "NSS activity points lookup",
  description: "Find your USN across the NSS activity points lists",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
