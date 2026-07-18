import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, DM_Mono, DM_Sans, Fraunces, IBM_Plex_Mono, IBM_Plex_Sans, Inter, Playwrite_TZ, Source_Code_Pro } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import "./auth/auth-portal.css";
import { Providers } from "./providers";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Pinch zoom stays enabled: locking scale is a WCAG 1.4.4 violation.
  themeColor: "rgb(244 243 239)",
  colorScheme: "light dark",
};

const ibmPlexSans = IBM_Plex_Sans({
  weight: ["400", "500", "600", "700"],
  variable: "--font-ibm-plex-sans",
  subsets: ["latin"],
});

const sourceCodePro = Source_Code_Pro({
  weight: ["400", "500"],
  variable: "--font-source-code-pro",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  weight: ["300", "400"],
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-landing-serif",
});

const inter = Inter({
  weight: ["300", "400", "500"],
  subsets: ["latin"],
  variable: "--font-landing-sans",
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

const dmMono = DM_Mono({
  weight: ["400", "500"],
  variable: "--font-dm-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "Lemma",
    template: "%s | Lemma",
  },
  applicationName: "Lemma",
  description:
    "Build AI pods for long-running agentic work. Design agents, records, workflows, and apps in one place.",
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
      className={`${bricolageGrotesque.variable} ${dmMono.variable} ${ibmPlexSans.variable} ${sourceCodePro.variable} ${fraunces.variable} ${inter.variable} ${ibmPlexMono.variable} ${playwriteTz.variable} ${documentSans.variable}`}
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
