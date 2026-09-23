import type { NextConfig } from "next";
import path from "node:path";

const config: NextConfig = {
    poweredByHeader: false,
    transpilePackages: ["lemma-sdk"],
    turbopack: { root: path.resolve(process.cwd(), "..") },
    async headers() {
        return [{
            source: "/demo/:path*",
            headers: [{ key: "Content-Security-Policy", value: "connect-src 'self'; form-action 'self'; frame-ancestors 'self'" }],
        }, {
            source: "/connector-logos/:path*",
            headers: [{ key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" }],
        }];
    },
};
export default config;
