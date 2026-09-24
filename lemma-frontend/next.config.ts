import type { NextConfig } from "next";
import path from "node:path";

const config: NextConfig = {
  poweredByHeader: false,
  skipTrailingSlashRedirect: true,
  async redirects() {
    return [
      { source: "/terms", destination: "/tos", permanent: true },
      { source: "/llm.txt", destination: "/llms.txt", permanent: true },
      { source: "/login", destination: "/auth", permanent: false },
      { source: "/signup", destination: "/auth/signup", permanent: false },
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
      { source: "/landing", destination: "/", permanent: true },
      ...["home", "pods", "conversations"].map((p) => ({
        source: "/" + p,
        destination: "/t",
        permanent: false,
      })),
    ];
  },
  async rewrites() {
    const ingest =
      process.env.NEXT_PUBLIC_ANALYTICS_INGEST_HOST ||
      "https://eu.i.posthog.com";
    const assets =
      process.env.NEXT_PUBLIC_ANALYTICS_ASSETS_HOST ||
      "https://eu-assets.i.posthog.com";
    return [
      {
        source: "/ingest/static/:path*",
        destination: assets + "/static/:path*",
      },
      { source: "/ingest/array/:path*", destination: assets + "/array/:path*" },
      { source: "/ingest/:path*", destination: ingest + "/:path*" },
    ];
  },
  transpilePackages: ["lemma-sdk"],
  turbopack: { root: path.resolve(process.cwd(), "..") },
  async headers() {
    return [
      {
        source: "/demo/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value:
              "connect-src 'self'; form-action 'self'; frame-ancestors 'self'",
          },
        ],
      },
      {
        source: "/connector-logos/:path*",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=86400, stale-while-revalidate=604800",
          },
        ],
      },
    ];
  },
};
export default config;
