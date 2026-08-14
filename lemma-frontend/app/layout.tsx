import type { Metadata, Viewport } from "next";
// Product and landing share one voice: Inter for everything, DM Mono for
// machine values. See the typography note in styles/tokens.css. The rest are
// landing-only display faces.
//
// Loaded from `next/font/local` over Fontsource packages rather than
// `next/font/google`, because the Google loader fetches from
// fonts.googleapis.com *during the build*. That made every build only as
// reliable as Google's CDN, and it is not reliable: it took down six of our
// last eight release runs. The failures are 404s on asset URLs that Google's
// own CSS had listed moments earlier -- an edge serving one generation of the
// stylesheet while the assets it names have already been rotated away -- so
// they are per-edge, unreproducible, and immune to retries beyond luck.
// Vercel's own guidance for CI is to self-host, and there is no Next option
// that makes the fetch deterministic.
//
// Fontsource ships the same Google-hosted files as versioned npm packages, so
// the bytes are pinned in package-lock.json and the build makes no network
// request at all. `npm run build` now succeeds with fonts.googleapis.com
// blocked, which is the property being bought.
import localFont from "next/font/local";
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
// Variable files carry the whole weight axis in one request, so the weights
// each face declares below are the range the family actually supports rather
// than the handful the design uses. `wght` is the pure weight axis: the `opsz`
// and `full` variants Fontsource also ships add an optical-size axis that
// would render differently from the static weights this replaced.
// Subsetted and axis-trimmed by scripts/subset-inter.sh, and committed —
// this is the one face preloaded on every route, so its bytes are on the
// critical path for every visitor.
//
// Fontsource ships the file Google publishes; Google *serves* a tighter cut of
// it. Taking the package file as-is put 47.1 KB on that path against the
// 35.2 KB the Google loader used to fetch, which the bundle budget correctly
// refused. The script re-cuts it to Google's own `latin` unicode-range and
// narrows the weight axis to the 300-600 the design has always had, landing
// under the previous figure. `weight` below must match that axis.
const inter = localFont({
  src: "./fonts/inter-latin-wght-normal.subset.woff2",
  weight: "300 600",
  display: "swap",
  variable: "--font-landing-sans",
});

// Mono face — code, tables and machine values in the assistant.
const dmMono = localFont({
  src: [
    { path: "../node_modules/@fontsource/dm-mono/files/dm-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../node_modules/@fontsource/dm-mono/files/dm-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
  ],
  display: "swap",
  variable: "--font-dm-mono",
});

// Reading serif — body copy on the blog, changelog and docs.
//
// Fraunces below is a *display* serif: it carries the landing's headlines and
// is far too characterful to read a thousand words in. Source Serif 4 was drawn
// for continuous reading on screen and shares Inter's vertical proportions, so
// a sans headline over serif body sits on one baseline rhythm instead of
// looking like two fonts that met by accident.
const sourceSerif = localFont({
  src: [
    { path: "../node_modules/@fontsource-variable/source-serif-4/files/source-serif-4-latin-wght-normal.woff2", weight: "200 900", style: "normal" },
    { path: "../node_modules/@fontsource-variable/source-serif-4/files/source-serif-4-latin-wght-italic.woff2", weight: "200 900", style: "italic" },
  ],
  display: "swap",
  variable: "--font-reading-serif",
  preload: false,
});

// Landing display serif.
const fraunces = localFont({
  src: [
    { path: "../node_modules/@fontsource-variable/fraunces/files/fraunces-latin-wght-normal.woff2", weight: "100 900", style: "normal" },
    { path: "../node_modules/@fontsource-variable/fraunces/files/fraunces-latin-wght-italic.woff2", weight: "100 900", style: "italic" },
  ],
  display: "swap",
  variable: "--font-landing-serif",
  preload: false,
});

// Landing mono.
const ibmPlexMono = localFont({
  src: [
    { path: "../node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
  ],
  display: "swap",
  variable: "--font-landing-mono",
  preload: false,
});

// The handwritten greeting, on one landing block. Static rather than variable:
// Playwrite has no variable build, which is also why the Google loader gave it
// no `subsets` option and never preloaded it. `preload: false` says here what
// the loader used to decide on its own.
const playwriteTz = localFont({
  src: [
    { path: "../node_modules/@fontsource/playwrite-tz/files/playwrite-tz-latin-300-normal.woff2", weight: "300", style: "normal" },
    { path: "../node_modules/@fontsource/playwrite-tz/files/playwrite-tz-latin-400-normal.woff2", weight: "400", style: "normal" },
  ],
  display: "swap",
  variable: "--font-greeting-hand",
  preload: false,
});

// Long-form document and legal pages.
const documentSans = localFont({
  src: "../node_modules/@fontsource-variable/dm-sans/files/dm-sans-latin-wght-normal.woff2",
  weight: "100 1000",
  display: "swap",
  variable: "--font-document-sans",
  preload: false,
});

const bricolageGrotesque = localFont({
  src: "../node_modules/@fontsource-variable/bricolage-grotesque/files/bricolage-grotesque-latin-wght-normal.woff2",
  weight: "200 800",
  display: "swap",
  variable: "--font-bricolage-grotesque",
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
      className={`${bricolageGrotesque.variable} ${fraunces.variable} ${sourceSerif.variable} ${inter.variable} ${dmMono.variable} ${ibmPlexMono.variable} ${playwriteTz.variable} ${documentSans.variable}`}
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
