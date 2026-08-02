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

/**
 * Every family below is declared here so its CSS variable is global — the token
 * chain in `styles/tokens.css` resolves against all of them, and a family scoped
 * to a route group would silently fall back to system text wherever else it is
 * referenced.
 *
 * `preload` is a separate decision from declaring. A preloaded family emits
 * `<link rel="preload">` into every document, so it competes for bandwidth on
 * the critical path of routes that never paint a single glyph of it. Only the
 * faces that `--font-body-family`, `--font-display-family` and
 * `--font-mono-family` actually resolve to earn that:
 *
 *   body    → Inter → system
 *   display → Inter → system
 *   mono    → DM Mono → ui-monospace
 *
 * The rest are landing, legal, greeting and document faces: no app route paints
 * them above the fold. They still load the moment a matching rule applies —
 * `preload: false` removes the eager fetch, not the font.
 */

// Body *and* display face — product and landing share one voice. 600 exists
// because landing reserves it for the fake product chrome in its mockups, and
// dense real UI needs Inter's own semibold rather than a synthesized bold.
const inter = Inter({
  weight: ["300", "400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-landing-sans",
});

// Mono face — code, tables and machine values in the assistant.
const dmMono = DM_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-dm-mono",
});

// Landing display serif.
const fraunces = Fraunces({
  weight: ["300", "400"],
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-landing-serif",
  preload: false,
});

// Landing mono.
const ibmPlexMono = IBM_Plex_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-landing-mono",
  preload: false,
});

// The handwritten greeting, on one landing block.
const playwriteTz = Playwrite_TZ({
  weight: ["300", "400"],
  variable: "--font-greeting-hand",
  preload: false,
});

// Long-form document and legal pages.
const documentSans = DM_Sans({
  weight: ["300", "400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-document-sans",
  preload: false,
});

const bricolageGrotesque = Bricolage_Grotesque({
  weight: ["400", "500", "700", "800"],
  variable: "--font-bricolage-grotesque",
  subsets: ["latin"],
  preload: false,
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
