import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { EventEmitter } from 'node:events';
import { attachVoiceGateway } from './server/voice-gateway.mjs';
import { attachLiveGateway } from './server/live-gateway.mjs';

const dev = process.argv.includes('--dev');
const portIndex = process.argv.indexOf('--port');
const port = Number(portIndex >= 0 ? process.argv[portIndex + 1] : process.env.PORT || 3000);
const here = path.dirname(fileURLToPath(import.meta.url));

/* Two trees run this file. A checkout (and the hosted image, which copies
 * `next.config.ts` beside it) lets Next load its config the usual way. The
 * standalone tree the desktop host pack ships has no config file and no
 * TypeScript to load one with -- Next's own generated `server.js` gets round
 * that by handing it the config it serialised at build time, through
 * `__NEXT_PRIVATE_STANDALONE_CONFIG`, and so does this. Without it `next()`
 * goes looking for a config file, finds none, and serves with defaults: no
 * redirects, no rewrites, and a `distDir` that is not where the build is.
 *
 * Recognised by the `server.js` Next writes only into a standalone tree, not
 * by the config file's absence: `scripts/complete-standalone.mjs` traces this
 * file, and a path spelled here is a path the tracer copies -- naming the
 * config file put it into the tree this check was meant to recognise.
 *
 * NODE_ENV and the working directory are set before Next is imported, not
 * after, for the same reason Next's generated server sets them first: Next
 * reads NODE_ENV as it loads, and the content loader resolves `content/`
 * against the working directory, which locald sets to the pack's `frontend/`
 * rather than to the directory this file is in. */
const requiredServerFiles = path.join(here, '.next/required-server-files.json');
const standalone = !dev && existsSync(path.join(here, 'server.js')) && existsSync(requiredServerFiles);
if (standalone) {
    const { config } = JSON.parse(readFileSync(requiredServerFiles, 'utf8'));
    process.env.__NEXT_PRIVATE_STANDALONE_CONFIG = JSON.stringify({ ...config, distDir: './.next' });
    process.env.NODE_ENV = 'production';
    process.chdir(here);
}
const { default: next } = await import('next');

/* Where to listen. Unset listens everywhere, which is what the hosted image
 * wants: omitting the host lets Node accept both IPv6 localhost and IPv4.
 * Desktop sets it to loopback -- its gateway is what faces the LAN when
 * sharing is on, and this server answering on every interface regardless
 * would publish the workspace to the network with sharing off. A variable of
 * its own rather than `HOSTNAME`, which a container runtime sets to the
 * container's own name. */
const host = process.env.LEMMA_FRONTEND_HOST?.trim() || undefined;

// Next installs its own upgrade listener after the first HTTP request. Keep
// that listener on a separate event bus so it never consumes voice sockets.
const nextUpgrades = new EventEmitter();
const app = next({ dev, dir: here, hostname: host ?? '0.0.0.0', port, httpServer: nextUpgrades });
await app.prepare(); // Loads Next's environment files before voice connections start.
const handle = app.getRequestHandler();
const server = createServer((request, response) => handle(request, response));
// One per voice model. Each gateway claims its own path and ignores the
// other's, so which one the browser opens is the browser's choice —
// NEXT_PUBLIC_VOICE_PROVIDER decides it, and both can be running at once.
const gateways = [attachVoiceGateway(server), attachLiveGateway(server)];
const VOICE_PATHS = new Set(['/api/voice', '/api/live']);
server.on('upgrade', (request, socket, head) => {
    if (!VOICE_PATHS.has(new URL(request.url, 'http://localhost').pathname)) nextUpgrades.emit('upgrade', request, socket, head);
});
server.listen(port, host, () => console.log(`Lemma listening on ${host ?? '*'}:${port}`));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
    for (const gateway of gateways) for (const client of gateway.clients) client.close(1001, 'Server restarting');
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
});
