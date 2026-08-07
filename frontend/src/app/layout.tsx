import type { Metadata } from "next";
import "./globals.css";
import Providers from "@/components/Providers";

export const metadata: Metadata = {
  title: "Verdict — Adversarial Research Audit",
  description:
    "Upload a research paper and watch three AI personas debate its merits, " +
    "producing citation-grounded verdicts and a structured debrief.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
