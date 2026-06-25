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
