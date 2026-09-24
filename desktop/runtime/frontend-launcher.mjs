import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// Two shapes, one launcher. A released pack hands us the custom server
// (`server.mjs`) at the top of lemma-frontend's standalone tree; `--dev
// <projectDir>` runs that same file from a checkout with `--dev` instead, for
// desktop local-mode development. Both go through here because the
// runtime-config.js written below is locald's frontend health check, and a
// second copy of that contract would be a second thing to keep in step.
//
// The custom server rather than Next's generated `server.js`, in both shapes:
// only it carries the voice and live-call WebSocket gateways, and a frontend
// started any other way serves every page and 404s every call.
const devMode = process.argv[2] === "--dev";
const target = process.argv[devMode ? 3 : 2] ? resolve(process.argv[devMode ? 3 : 2]) : "";
if (!target) {
  throw new Error(
    devMode
      ? "frontend launcher --dev requires the frontend project directory"
      : "frontend launcher requires the frontend server path",
  );
}
const server = devMode ? join(target, "server.mjs") : target;

// locald says where to listen with HOSTNAME, the variable Next's generated
// server reads. The custom server reads its own variable instead -- in a
// container HOSTNAME is the container's name -- so translate it here, where
// HOSTNAME can only have come from locald. Loopback is the point: sharing puts
// a gateway in front of this server, and it must not answer the LAN itself.
if (process.env.HOSTNAME && !process.env.LEMMA_FRONTEND_HOST) {
  process.env.LEMMA_FRONTEND_HOST = process.env.HOSTNAME;
}

if (process.env.LEMMA_LOCALD_PARENT_WATCHDOG === "1") {
  process.stdin.resume();
  process.stdin.once("end", () => process.exit(0));
  process.stdin.once("error", () => process.exit(0));
}

if (!process.env.NEXT_PUBLIC_API_URL || !process.env.NEXT_PUBLIC_SITE_URL) {
  throw new Error("locald must provide the isolated frontend and API origins");
}
// Defaulted in the environment the server inherits, not only in the file
// written below: the frontend reads its origins from its own environment at
// start (`/site-config.js`), and runtime-config.js is only the health check.
process.env.NEXT_PUBLIC_AUTH_URL ||= `${process.env.NEXT_PUBLIC_SITE_URL}/auth`;
process.env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN ??= "";
const publicEnv = {};
for (const [key, value] of Object.entries(process.env)) {
  if (key.startsWith("NEXT_PUBLIC_")) publicEnv[key] = value ?? "";
}

const runtimeConfig = `window.__ENV = ${JSON.stringify(publicEnv, null, 2)};\n`;
const applicationIds = (process.env.MICROSOFT_APPLICATION_IDS ?? "")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean)
  .map((applicationId) => ({ applicationId }));
const identityConfig = `${JSON.stringify({ associatedApplications: applicationIds }, null, 2)}\n`;

// Next resolves public assets relative to the directory the server runs from,
// which is the one containing server.mjs -- the project itself in dev. Also
// populate the root public tree for compatibility with older packs.
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
  // server.mjs reads PORT from the environment, which locald already sets to
  // the port its health check polls. The same Node that runs this launcher,
  // not whichever `node` is first on PATH, and no `npx`: there is nothing to
  // resolve, and a shim in between is one more process the watchdog below
  // would have to reach through. Inherit stdio so compile errors reach the
  // locald log rather than vanishing.
  const next = spawn(process.execPath, [server, "--dev"], {
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
