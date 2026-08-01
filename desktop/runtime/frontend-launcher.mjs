import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// Two shapes, one launcher. A released pack hands us a built Next standalone
// server; `--dev <projectDir>` runs a checkout's `next dev` instead, for
// desktop local-mode development. Both go through here because the
// runtime-config.js written below is locald's frontend health check, and a
// second copy of that contract would be a second thing to keep in step.
const devMode = process.argv[2] === "--dev";
const target = process.argv[devMode ? 3 : 2] ? resolve(process.argv[devMode ? 3 : 2]) : "";
if (!target) {
  throw new Error(
    devMode
      ? "frontend launcher --dev requires the frontend project directory"
      : "frontend launcher requires the Next.js server path",
  );
}
const server = devMode ? "" : target;

if (process.env.LEMMA_LOCALD_PARENT_WATCHDOG === "1") {
  process.stdin.resume();
  process.stdin.once("end", () => process.exit(0));
  process.stdin.once("error", () => process.exit(0));
}

const publicEnv = {};
for (const [key, value] of Object.entries(process.env)) {
  if (key.startsWith("NEXT_PUBLIC_")) publicEnv[key] = value ?? "";
}
if (!publicEnv.NEXT_PUBLIC_API_URL || !publicEnv.NEXT_PUBLIC_SITE_URL) {
  throw new Error("locald must provide the isolated frontend and API origins");
}
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
// Also populate the root public tree for compatibility with older packs. In dev
// the project's own public/ is the one Next serves.
const root = process.cwd();
const publicDirs = new Set(
  devMode ? [join(target, "public")] : [join(root, "public"), join(dirname(server), "public")],
);
for (const publicDir of publicDirs) {
  mkdirSync(join(publicDir, ".well-known"), { recursive: true });
  writeFileSync(join(publicDir, "runtime-config.js"), runtimeConfig, { mode: 0o600 });
  writeFileSync(
    join(publicDir, ".well-known", "microsoft-identity-association.json"),
    identityConfig,
    { mode: 0o600 },
  );
}

if (!devMode) {
  await import(pathToFileURL(server).href);
} else {
  // Next reads PORT from the environment, which locald already sets to the
  // port its health check polls. Inherit stdio so compile errors reach the
  // locald log rather than vanishing.
  const next = spawn("npx", ["next", "dev"], {
    cwd: target,
    env: process.env,
    stdio: "inherit",
  });
  // The watchdog above exits this process when locald closes stdin; carry the
  // child with it, or an orphaned dev server keeps the port and every later
  // launch fails its health check.
  const stop = (signal) => {
    if (!next.killed) next.kill(signal);
  };
  process.once("exit", () => stop("SIGTERM"));
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      stop(signal);
      process.exit(0);
    });
  }
  next.once("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
}
