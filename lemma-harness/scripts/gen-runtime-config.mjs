#!/usr/bin/env node
/**
 * Generates public/runtime-config.js (window.__ENV) from the local env files
 * (.env, .env.local, .env.development, ...) so that `next dev` and `next start`
 * behave like the Docker image, where docker-entrypoint.sh writes this file at
 * container start.
 *
 * Why this exists: on the client, lib/config.ts reads window.__ENV BEFORE
 * process.env. Without this file the browser would fall back to a stale,
 * gitignored runtime-config.js (or hard-coded defaults), so a freshly-edited
 * .env.local would only apply to SSR and silently not to the browser. Running
 * this on `predev` keeps .env.local as the single source of truth for both.
 *
 * The file is gitignored (see .gitignore) and safe to overwrite.
 */
import { writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import nextEnv from "@next/env";

const { loadEnvConfig } = nextEnv;

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

// Load .env / .env.local / .env.development exactly like Next.js does.
loadEnvConfig(root, true /* dev */);

const env = {};
for (const [key, value] of Object.entries(process.env)) {
  if (key.startsWith("NEXT_PUBLIC_")) {
    env[key] = value ?? "";
  }
}

// Mirror the defaults in docker-entrypoint.sh so dev and prod agree.
env.NEXT_PUBLIC_API_URL ||= "https://api.localhost";
env.NEXT_PUBLIC_SITE_URL ||= "http://localhost:3000";
env.NEXT_PUBLIC_AUTH_URL ||= env.NEXT_PUBLIC_SITE_URL;
env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN ||= "";

const out = join(root, "public", "runtime-config.js");
writeFileSync(out, `window.__ENV = ${JSON.stringify(env, null, 2)};\n`);

console.log(`[runtime-config] wrote ${out}`);
console.log(`[runtime-config]   NEXT_PUBLIC_API_URL  = ${env.NEXT_PUBLIC_API_URL}`);
console.log(`[runtime-config]   NEXT_PUBLIC_SITE_URL = ${env.NEXT_PUBLIC_SITE_URL}`);
console.log(`[runtime-config]   NEXT_PUBLIC_AUTH_URL = ${env.NEXT_PUBLIC_AUTH_URL}`);
