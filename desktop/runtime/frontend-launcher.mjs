import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const server = process.argv[2] ? resolve(process.argv[2]) : "";
if (!server) {
  throw new Error("frontend launcher requires the Next.js server path");
}

const publicEnv = {};
for (const [key, value] of Object.entries(process.env)) {
  if (key.startsWith("NEXT_PUBLIC_")) publicEnv[key] = value ?? "";
}
publicEnv.NEXT_PUBLIC_API_URL ||= "http://api.lemma.localhost:8711";
publicEnv.NEXT_PUBLIC_SITE_URL ||= "http://app.lemma.localhost:3711";
publicEnv.NEXT_PUBLIC_AUTH_URL ||= `${publicEnv.NEXT_PUBLIC_SITE_URL}/auth`;
publicEnv.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN ||= "";

const runtimeConfig = `window.__ENV = ${JSON.stringify(publicEnv, null, 2)};\n`;
const applicationIds = (process.env.MICROSOFT_APPLICATION_IDS ?? "")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean)
  .map((applicationId) => ({ applicationId }));
const identityConfig = `${JSON.stringify({ associatedApplications: applicationIds }, null, 2)}\n`;

// Next resolves public assets relative to the directory containing server.js.
// Also populate the root public tree for compatibility with older packs.
const root = process.cwd();
const publicDirs = new Set([join(root, "public"), join(dirname(server), "public")]);
for (const publicDir of publicDirs) {
  mkdirSync(join(publicDir, ".well-known"), { recursive: true });
  writeFileSync(join(publicDir, "runtime-config.js"), runtimeConfig, { mode: 0o600 });
  writeFileSync(
    join(publicDir, ".well-known", "microsoft-identity-association.json"),
    identityConfig,
    { mode: 0o600 },
  );
}

await import(pathToFileURL(server).href);
