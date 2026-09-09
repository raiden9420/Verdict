import type { Metadata } from "next";
import "./globals.css";
import Providers from "@/components/Providers";
import { ThemeProvider } from "@/components/ThemeProvider";
import { THEME_BOOTSTRAP_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "Verdict — Evidence-led research review",
  description:
    "A structured adversarial review of your research paper. Inspect findings, trace source evidence, and plan your next revision.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          id="theme-bootstrap"
          dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP_SCRIPT }}
        />
      </head>
      <body>
        <ThemeProvider>
          <Providers>{process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_LOCAL_PREVIEW === "1" && <div className="local-preview-notice">Local test workspace · Synthetic data only · preview@example.test / preview-only</div>}{children}</Providers>
        </ThemeProvider>
      </body>
    </html>
  );
}
