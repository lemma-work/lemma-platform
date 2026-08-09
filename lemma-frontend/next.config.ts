import type { NextConfig } from "next";
import path from "node:path";

const devOrigins: string[] = [
  "localhost",
  "127.0.0.1",
  "127.0.0.2",
  "127.0.0.3",
  "127.0.1.1",
  "127.0.2.2",
  "127.0.2.3",
  "127.1",
  "127.0.0.1.nip.io",
  "127-0-0-1.sslip.io",
  "127-0-0-2.sslip.io",
  "127-0-0-3.sslip.io",
];

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL;
if (siteUrl) {
  try {
    devOrigins.push(new URL(siteUrl).hostname);
  } catch {}
}

const authUrl = process.env.NEXT_PUBLIC_AUTH_URL;
if (authUrl) {
  try {
    devOrigins.push(new URL(authUrl).hostname);
  } catch {}
}

const apiUrl = process.env.NEXT_PUBLIC_API_URL;
if (apiUrl) {
  try {
    devOrigins.push(new URL(apiUrl).hostname);
  } catch {}
}

const nextConfig: NextConfig = {
  allowedDevOrigins: devOrigins,
  output: "standalone",
  transpilePackages: ["lemma-sdk"],
  async redirects() {
    return [
      {
        source: "/verify-email",
        destination: "/auth/verify-email",
        permanent: false,
      },
      {
        source: "/reset-password",
        destination: "/auth/reset-password",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    // Same-origin analytics ingestion. Ad blockers drop a meaningful share of
    // direct calls to an analytics vendor, and the share they drop skews toward
    // the technical users Lemma sells to, so the loss is not random noise.
    // Local deployments never initialise the client, so this proxies nothing
    // there (see lib/analytics/client.ts).
    const analyticsHost =
      process.env.NEXT_PUBLIC_ANALYTICS_INGEST_HOST || "https://eu.i.posthog.com";
    return [
      {
        source: "/ingest/static/:path*",
        destination: `${analyticsHost}/static/:path*`,
      },
      { source: "/ingest/:path*", destination: `${analyticsHost}/:path*` },
    ];
  },
  serverExternalPackages: ["esbuild"],
  turbopack: {
    root: path.resolve(process.cwd(), ".."),
  },
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "logos.composio.dev",
      },
      {
        protocol: "https",
        hostname: "picsum.photos",
      },
    ],
    // Composio logos are SVGs; Next blocks SVG optimization unless explicitly enabled.
    dangerouslyAllowSVG: true,
    contentDispositionType: "attachment",
    contentSecurityPolicy: "default-src 'self'; script-src 'none'; sandbox;",
  },
};

export default nextConfig;
