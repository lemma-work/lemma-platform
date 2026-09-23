import { spawn } from 'node:child_process';
import { statSync, watch } from 'node:fs';

/* Restarts the app server when the app server changes.
 *
 * Next's hot reload covers `src/`. It does not cover this directory, because
 * the custom server is a plain Node process that reads these files once at
 * boot — so without this a gateway edit sits there doing nothing while
 * `✓ Compiled` scrolls past saying everything is fine.
 *
 * Not `node --watch`, which looks like the obvious answer and is a trap: watch
 * mode gives every forked child an IPC channel and has it report its imports
 * back as `{"watch:import": [...]}`. Next's jest-worker pool treats anything
 * arriving on that channel as its own protocol, reads `message[0]`, finds
 * undefined, and throws `Unexpected response from worker: undefined` on a
 * loop. So the restart lives out here instead, in a supervisor that uses
 * `spawn` rather than `fork` — no IPC channel to be confused by, and the
 * server it runs is an ordinary node process that knows none of this. */

const WATCHED = ['server', 'server.mjs'];
/* Editors save in bursts — write, rename, touch — and each one is an event. */
const SETTLE_MS = 150;

let child = null;
let timer = null;
let restarting = false;

function start() {
    child = spawn(process.execPath, ['server.mjs', ...process.argv.slice(2)], { stdio: 'inherit' });
    child.on('exit', (code, signal) => {
        child = null;
        if (restarting) { restarting = false; start(); return; }
        /* Exited on its own: a crash, or a port already taken. Follow it out
           rather than respawning into the same wall. */
        if (!signal) process.exit(code ?? 0);
    });
}

function restart() {
    clearTimeout(timer);
    timer = setTimeout(() => {
        if (!child) { start(); return; }
        console.log('\n[dev] server changed — restarting');
        restarting = true;
        child.kill('SIGTERM');
    }, SETTLE_MS);
}

for (const target of WATCHED) {
    watch(target, { recursive: statSync(target).isDirectory() }, restart);
}
start();

for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, () => { restarting = false; child?.kill(signal); process.exit(0); });
}
