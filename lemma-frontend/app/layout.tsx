import type { Metadata, Viewport } from "next";
// Product and landing share one voice: Inter for everything, DM Mono for
// machine values. See the typography note in styles/tokens.css. The rest are
// landing-only display faces.
import { Bricolage_Grotesque, DM_Mono, DM_Sans, Fraunces, IBM_Plex_Mono, Inter, Playwrite_TZ } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import "./auth/auth-portal.css";
import { Providers } from "./providers";
import { COMPANY_LEGAL_NAME } from "@/lib/company";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Pinch zoom stays enabled: locking scale is a WCAG 1.4.4 violation.
  // Matches --bg-canvas in each appearance: warm paper light, warm near-black
  // dark.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "rgb(242 239 231)" },
    { media: "(prefers-color-scheme: dark)", color: "rgb(19 19 17)" },
  ],
  colorScheme: "light dark",
};

const fraunces = Fraunces({
  weight: ["300", "400"],
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-landing-serif",
});

// 600 is new here. Landing deliberately stops at 500 — it reserves 600 for the
// fake product chrome inside its mockups. Now that the real product is set in
// Inter too, that weight has to actually exist or dense UI gets a synthesized
// bold instead of Inter's own semibold.
const inter = Inter({
  weight: ["300", "400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-landing-sans",
});

const dmMono = DM_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-dm-mono",
});

const ibmPlexMono = IBM_Plex_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-landing-mono",
});

const playwriteTz = Playwrite_TZ({
  weight: ["300", "400"],
  variable: "--font-greeting-hand",
});

const documentSans = DM_Sans({
  weight: ["300", "400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-document-sans",
});

const bricolageGrotesque = Bricolage_Grotesque({
  weight: ["400", "500", "700", "800"],
  variable: "--font-bricolage-grotesque",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL?.startsWith("http")
      ? process.env.NEXT_PUBLIC_SITE_URL
      : "https://lemma.work",
  ),
  title: {
    default: "Lemma",
    template: "%s | Lemma",
  },
  applicationName: "Lemma",
  publisher: COMPANY_LEGAL_NAME,
  creator: COMPANY_LEGAL_NAME,
  authors: [{ name: COMPANY_LEGAL_NAME, url: "https://lemma.work" }],
  description:
    "Run your apps and agents. Bring your team.",
  keywords: [
    "Lemma",
    "AI agents",
    "agentic work",
    "operations",
    "internal tools",
    "AI pods",
  ],
  icons: {
    icon: [
      { url: "/favicon.ico" },
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: [{ url: "/apple-icon.png", sizes: "180x180", type: "image/png" }],
    shortcut: ["/favicon.ico"],
  },
  manifest: "/manifest.webmanifest",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      data-scroll-behavior="smooth"
      className={`${bricolageGrotesque.variable} ${fraunces.variable} ${inter.variable} ${dmMono.variable} ${ibmPlexMono.variable} ${playwriteTz.variable} ${documentSans.variable}`}
    >
      <head>
        <Script src="/runtime-config.js" strategy="beforeInteractive" />
      </head>
      <body className="font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
