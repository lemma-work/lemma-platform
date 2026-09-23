import { createServer } from 'node:http';
import next from 'next';
import { EventEmitter } from 'node:events';
import { attachVoiceGateway } from './server/voice-gateway.mjs';
import { attachLiveGateway } from './server/live-gateway.mjs';

const dev = process.argv.includes('--dev');
const portIndex = process.argv.indexOf('--port');
const port = Number(portIndex >= 0 ? process.argv[portIndex + 1] : process.env.PORT || 3000);
// Next installs its own upgrade listener after the first HTTP request. Keep
// that listener on a separate event bus so it never consumes voice sockets.
const nextUpgrades = new EventEmitter();
const app = next({ dev, hostname: '0.0.0.0', port, httpServer: nextUpgrades });
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
// Omit the host so Node accepts both IPv6 localhost and IPv4 connections.
server.listen(port, () => console.log(`Lemma listening on port ${port}`));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
    for (const gateway of gateways) for (const client of gateway.clients) client.close(1001, 'Server restarting');
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
});
