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
  // Required by the /ingest rewrites below: PostHog's ingestion endpoints rely
  // on trailing slashes (`/batch/`, `/decide/`), and Next's default 307 to the
  // slash-less form breaks them. Without this the proxy looks configured and
  // silently delivers nothing.
  skipTrailingSlashRedirect: true,
  async rewrites() {
    // Same-origin analytics ingestion. Ad blockers drop a meaningful share of
    // direct calls to an analytics vendor, and the share they drop skews toward
    // the technical users Lemma sells to, so the loss is not random noise.
    //
    // Both hosts are read at BUILD time, not run time: Next serialises rewrites
    // into `routes-manifest.json` and the runtime server never re-evaluates this
    // function. The Docker builder stage has no NEXT_PUBLIC_* set, so overriding
    // these on a container does nothing — which is also why this rewrite is NOT
    // gated on the analytics key. A build-time gate would evaluate to "no key"
    // in every image, including Cloud's, and kill analytics with no error
    // anywhere. Unconfigured deployments are already inert: with no key the
    // client never initialises, so nothing ever requests /ingest and this
    // proxies zero bytes.
    const ingestHost =
      process.env.NEXT_PUBLIC_ANALYTICS_INGEST_HOST || "https://eu.i.posthog.com";
    // Assets live on a *different* host from ingestion. Pointing /static at the
    // ingest host — as this did — misroutes the remote-config bootstrap.
    const assetsHost =
      process.env.NEXT_PUBLIC_ANALYTICS_ASSETS_HOST || "https://eu-assets.i.posthog.com";
    return [
      { source: "/ingest/static/:path*", destination: `${assetsHost}/static/:path*` },
      { source: "/ingest/array/:path*", destination: `${assetsHost}/array/:path*` },
      { source: "/ingest/:path*", destination: `${ingestHost}/:path*` },
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
