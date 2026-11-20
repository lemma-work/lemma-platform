#!/usr/bin/env node
/**
 * The stream bench: what the live browser view delivers, measured from the
 * viewer's side, scenario by scenario.
 *
 * Opens the Computer tab in a real browser against a target, with the viewer
 * probe on (`lemma-frontend/src/computer/stream-probe.ts`, `?streamProbe=1`,
 * dev builds only), while the sandbox shows the probe page
 * (`lemma-backend/sandbox-images/bench/`). Runs each scenario at each viewer
 * density under each network profile and writes `results.json` and
 * `results.md` to a fresh directory under `--out`.
 *
 *   node scripts/stream-bench.mjs --target out/stream-bench/target.json
 *
 * A target is JSON: `api_url`, `token` (a bearer token for a throwaway bench
 * user), `pod_id`, and `probe_url` — where the runner reaches the probe
 * page's server in the sandbox, for mode switches, clock pings and CPU. The
 * local one is written by
 * `lemma-backend/app/modules/workspace/tests/e2e/stream_bench_stack.py`.
 *
 * Options:
 *   --frontend <url>      a frontend already serving this branch in dev mode;
 *                         without it one is started on localhost:4173
 *   --out <dir>           default out/stream-bench
 *   --dpr 1,2             viewer densities
 *   --profiles a,b        unthrottled, 20mbps, 5mbps (default: all three)
 *   --scenarios a,b       idle, scroll, animation, typing, resize (default: all)
 *   --quick               5 s scenarios, 10 keys, 2 resizes: a smoke run
 *   --shaper proxy|cdp    how profiles are applied (default proxy; see PROFILES)
 *   --transports a,b      vnc, webrtc (default vnc). WebRTC needs a target with
 *                         a `turn_container` for its profiles: its media is
 *                         shaped by `tc netem` on the TURN server, since it
 *                         never crosses the TCP proxy
 *   --headed              show the browser
 *
 * `STREAM_BENCH_DEBUG=1` logs a stack trace whenever the page closes the view
 * socket itself -- how a stream the decoder rejects shows up.
 *
 * Playwright is the frontend's dev dependency and is borrowed from there:
 * nothing outside the bench depends on it.
 */

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import net from "node:net";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";

const REPO = path.resolve(import.meta.dirname, "..");
const FRONTEND = path.join(REPO, "lemma-frontend");
const { chromium } = createRequire(path.join(FRONTEND, "package.json"))("playwright");

/* Network profiles. `latency` is round-trip, throughputs are bytes/s.
 *
 * Measured on this bench (2026-10-21): CDP's `Network.emulateNetworkConditions`
 * throttles a WebSocket's throughput but adds no latency to frames on a socket
 * that is already open -- a key reached the sandbox in 2 ms under "150 ms" --
 * and its `packetLoss` is honoured for WebRTC only. So by default the runner
 * shapes the API connection itself (`shapingProxy` below) and leaves CDP off;
 * `--shaper cdp` keeps the CDP-only behaviour for comparison. */
const PROFILES = {
    unthrottled: { label: "unthrottled", latency: 0, downloadThroughput: -1, uploadThroughput: -1, packetLoss: 0 },
    "20mbps": { label: "20 Mbps / 60 ms", latency: 60, downloadThroughput: 20e6 / 8, uploadThroughput: 20e6 / 8, packetLoss: 0 },
    "5mbps": { label: "5 Mbps / 150 ms / 1% loss", latency: 150, downloadThroughput: 5e6 / 8, uploadThroughput: 5e6 / 8, packetLoss: 1 },
};
const shaped = (profile) => profile.latency > 0 || profile.downloadThroughput > 0 || profile.packetLoss > 0;

/** Stall a TCP stream for a lost segment: Linux's minimum retransmission
 *  timeout. A modelled loss holds back everything behind it, as TCP would. */
const LOSS_STALL_MS = 200;
const SEGMENT_BYTES = 1448;

/** A TCP proxy in front of the API that applies a profile to both directions:
 *  half the round trip each way, serialisation at the profile's throughput,
 *  and each segment lost with the profile's probability stalling the stream.
 *  An approximation of a real link -- no congestion control reacts to the
 *  loss -- but one that applies to every byte of the socket the picture
 *  arrives on, which CDP's emulation does not. */
async function shapingProxy(upstream, profile) {
    const queueLimit = 256 * 1024;
    /* One FIFO per direction, drained by one timer in order. A timer per
       chunk reorders the stream: Node does not order timers of different
       durations that fall due together, and RFB has no way back from that. */
    const pipe = (from, to, bytesPerSecond) => {
        const queue = [];
        let free = 0;
        let last = 0;
        let queued = 0;
        let timer = null;
        let ended = false;
        const drain = () => {
            timer = null;
            const at = now();
            while (queue.length && queue[0].release <= at) {
                const { chunk } = queue.shift();
                if (!to.destroyed) to.write(chunk);
                queued -= chunk.length;
            }
            if (queued <= queueLimit && from.isPaused() && !ended) from.resume();
            if (queue.length) timer = setTimeout(drain, Math.max(0, queue[0].release - now()));
            else if (ended) to.end();
        };
        from.on("data", (chunk) => {
            const at = now();
            free = Math.max(free, at) + (bytesPerSecond > 0 ? (chunk.length / bytesPerSecond) * 1000 : 0);
            let release = Math.max(last, free + profile.latency / 2);
            const segments = Math.ceil(chunk.length / SEGMENT_BYTES);
            for (let i = 0; i < segments; i++) if (Math.random() * 100 < profile.packetLoss) release += LOSS_STALL_MS;
            last = release;
            queue.push({ chunk, release });
            queued += chunk.length;
            if (queued > queueLimit) from.pause();
            timer ??= setTimeout(drain, Math.max(0, queue[0].release - at));
        });
        /* A clean close is passed on only once everything queued ahead of it
           has gone; destroying at once would drop the delayed tail. */
        from.on("end", () => { ended = true; if (!queue.length && !timer) to.end(); });
        from.on("close", (failed) => { if (failed) to.destroy(); });
        from.on("error", () => undefined);
    };
    const server = net.createServer((client) => {
        const remote = net.connect(upstream.port, upstream.hostname);
        client.setNoDelay(true);
        remote.setNoDelay(true);
        pipe(client, remote, profile.uploadThroughput);
        pipe(remote, client, profile.downloadThroughput);
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    return { url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() };
}

const SCENARIOS = ["idle", "scroll", "animation", "typing", "resize"];
const VIEWPORT = { width: 1440, height: 900 };
const RESIZED = { width: 1120, height: 760 };
const GROUPS = ["x11vnc", "tigervnc", "websockify", "relay", "streamer", "xvfb", "chrome", "probe"];
/** The processes that exist to carry the picture, on either transport. */
const STREAMING = ["x11vnc", "tigervnc", "websockify", "relay", "streamer"];
const TRANSPORTS = ["vnc", "webrtc"];

function parseArgs(argv) {
    const args = { out: path.join(REPO, "out", "stream-bench"), dpr: [1, 2], profiles: Object.keys(PROFILES), scenarios: SCENARIOS, transports: ["vnc"] };
    for (let i = 0; i < argv.length; i++) {
        const flag = argv[i];
        const value = () => argv[++i];
        if (flag === "--target") args.target = value();
        else if (flag === "--frontend") args.frontend = value();
        else if (flag === "--out") args.out = path.resolve(value());
        else if (flag === "--dpr") args.dpr = value().split(",").map(Number);
        else if (flag === "--profiles") args.profiles = value().split(",");
        else if (flag === "--scenarios") args.scenarios = value().split(",");
        else if (flag === "--transports") args.transports = value().split(",");
        else if (flag === "--quick") args.quick = true;
        else if (flag === "--headed") args.headed = true;
        else if (flag === "--shaper") args.shaper = value();
        else throw new Error(`unknown option ${flag}`);
    }
    if (!args.target) throw new Error("--target <target.json> is required");
    args.shaper ??= "proxy";
    if (!["proxy", "cdp"].includes(args.shaper)) throw new Error("--shaper is proxy or cdp");
    for (const p of args.profiles) if (!PROFILES[p]) throw new Error(`unknown profile ${p}`);
    for (const s of args.scenarios) if (!SCENARIOS.includes(s)) throw new Error(`unknown scenario ${s}`);
    for (const t of args.transports) if (!TRANSPORTS.includes(t)) throw new Error(`unknown transport ${t}`);
    return args;
}

const now = () => performance.timeOrigin + performance.now();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const log = (...parts) => console.log(new Date().toISOString().slice(11, 19), ...parts);

function quantile(values, q) {
    const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
    if (!sorted.length) return null;
    const at = (sorted.length - 1) * q;
    const lo = Math.floor(at), hi = Math.ceil(at);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (at - lo);
}
const round = (value, digits = 1) => (value === null || value === undefined || !Number.isFinite(value) ? null : Number(value.toFixed(digits)));

/* ------------------------------------------------------------ the sandbox */

function probe(target) {
    const base = target.probe_url.replace(/\/?$/, "/");
    const call = async (route, init) => {
        const answer = await fetch(base + route, { signal: AbortSignal.timeout(10_000), ...init });
        if (!answer.ok) throw new Error(`${route}: ${answer.status} ${await answer.text()}`);
        return answer.json();
    };
    return {
        setMode: (mode) => call(`mode?m=${mode}`, { method: "POST" }),
        open: () => call("open", { method: "POST", signal: AbortSignal.timeout(100_000) }),
        stats: () => call("stats"),
        now: () => call("now"),
    };
}

/** The sandbox clock's offset from this one (sandbox minus here), from the
 *  ping with the shortest round trip, NTP-style: the sandbox read its clock
 *  somewhere inside that round trip, so half of it bounds the error. */
async function clockOffset(sandbox, pings = 10) {
    const samples = [];
    for (let i = 0; i < pings; i++) {
        const sent = now();
        const { now_ms: there } = await sandbox.now();
        const back = now();
        samples.push({ rtt: back - sent, offset: there - (sent + back) / 2 });
        await sleep(100);
    }
    samples.sort((a, b) => a.rtt - b.rtt);
    const best = samples[0];
    return {
        offset_ms: round(best.offset, 2),
        error_ms: round(best.rtt / 2, 2),
        pings,
        rtt_ms: { min: round(best.rtt, 2), median: round(quantile(samples.map((s) => s.rtt), 0.5), 2) },
        spread_ms: round(Math.max(...samples.map((s) => s.offset)) - Math.min(...samples.map((s) => s.offset)), 2),
    };
}

/** Cores used per streaming process group between two `/stats` readings. */
function cpuBetween(before, after) {
    const seconds = (after.now_ms - before.now_ms) / 1000;
    const cores = {};
    for (const group of GROUPS) {
        const a = after.groups[group]?.ticks, b = before.groups[group]?.ticks;
        cores[group] = a === undefined || b === undefined ? null : round((a - b) / after.clk_tck / seconds, 3);
    }
    const total = after.machine.total - before.machine.total;
    const idle = after.machine.idle - before.machine.idle;
    return {
        cores,
        streaming_cores: round(STREAMING.reduce((sum, g) => sum + (cores[g] ?? 0), 0), 3),
        machine_busy_fraction: total > 0 ? round(1 - idle / total, 3) : null,
        cpu_count: after.cpu_count,
        cpu_quota: after.cpu_quota,
        rss_mb: Object.fromEntries(GROUPS.map((g) => [g, after.groups[g] ? round(after.groups[g].rss_bytes / 2 ** 20, 0) : null])),
        page_paint_fps: after.page?.mode ? round(after.page.fps, 1) : null,
    };
}

/* ------------------------------------------------------------ the frontend */

async function startFrontend(target) {
    const port = 4173;
    /* `localhost`, not 127.0.0.1: Next's dev server serves its own chunks
       only to the origins it trusts, and the page never hydrates otherwise. */
    const origin = `http://localhost:${port}`;
    log(`starting the frontend on ${origin} against ${target.api_url}`);
    /* The app's own dev server, which is what `make dev` runs. */
    const server = spawn(process.execPath, ["server.mjs", "--dev", "--port", String(port)], {
        cwd: FRONTEND,
        env: { ...process.env, NEXT_PUBLIC_API_URL: target.api_url, NEXT_PUBLIC_DATA: "live", NEXT_TELEMETRY_DISABLED: "1" },
        stdio: ["ignore", "pipe", "pipe"],
        detached: true,
    });
    let output = "";
    server.stdout.on("data", (chunk) => { output += chunk; });
    server.stderr.on("data", (chunk) => { output += chunk; });
    const stop = () => { try { process.kill(-server.pid, "SIGTERM"); } catch { /* already gone */ } };
    process.once("exit", stop);
    const deadline = Date.now() + 180_000;
    while (true) {
        if (server.exitCode !== null) throw new Error(`the frontend exited:\n${output}`);
        try {
            /* The shell's route, not `/`: the marketing page is not what the
               bench opens, and a failure there says nothing about this. */
            if ((await fetch(origin + "/t", { signal: AbortSignal.timeout(120_000) })).status < 500) break;
        } catch { /* not up yet */ }
        if (Date.now() > deadline) throw new Error(`the frontend never answered:\n${output}`);
        await sleep(1000);
    }
    return { origin, stop };
}

/* ------------------------------------------------------------ one viewer */

/** Shape WebRTC's media, which never touches the TCP proxy: `tc netem` on
 *  the TURN server's interface, through a sidecar in its network namespace.
 *  Both ends are relay-only, so every media packet leaves that interface
 *  once each way -- half the round trip is added per direction, and the
 *  rate and loss apply to everything it sends. */
async function shapeTurn(target, profile) {
    if (!target.turn_container) return "none";
    const p = PROFILES[profile];
    const rule = shaped(p)
        ? ["qdisc", "replace", "dev", "eth0", "root", "netem", "delay", `${p.latency / 2}ms`,
            ...(p.downloadThroughput > 0 ? ["rate", `${Math.round((p.downloadThroughput * 8) / 1e6)}mbit`] : []),
            ...(p.packetLoss > 0 ? ["loss", `${p.packetLoss}%`] : [])]
        : ["qdisc", "del", "dev", "eth0", "root"];
    await new Promise((resolve) => {
        const child = spawn("docker", ["run", "--rm", "--cap-add", "NET_ADMIN", "--network", `container:${target.turn_container}`,
            target.netem_image ?? "lemma-bench-netem", "tc", ...rule], { stdio: "ignore" });
        child.on("close", resolve);
    });
    return shaped(p) ? "netem on TURN" : "none";
}

async function openViewer(browser, { frontend, target, dpr, profile, shaper, transport }) {
    /* The proxy needs a plain-HTTP API to sit in front of; an https target is
       shaped by CDP alone, and the results say so. */
    const useProxy = shaper === "proxy" && shaped(PROFILES[profile]) && target.api_url.startsWith("http:");
    const proxy = useProxy ? await shapingProxy(new URL(target.api_url), PROFILES[profile]) : null;
    const api = proxy?.url ?? target.api_url;
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: dpr });
    context.on("close", () => proxy?.close());
    /* A bench user's token on a bench stack, where the frontend's bearer slot
       is how a localhost origin authenticates at all. */
    await context.addInitScript(({ api, token }) => {
        localStorage.setItem("lemma_api_url", api);
        localStorage.setItem("lemma_token", token);
        sessionStorage.setItem("lemma_stream_probe", "1");
    }, { api, token: target.token });
    const page = await context.newPage();
    if (process.env.STREAM_BENCH_DEBUG) {
        await page.addInitScript(() => {
            const close = WebSocket.prototype.close;
            WebSocket.prototype.close = function (...args) {
                if (this.url.includes("/browser/view")) console.log("ws.close", JSON.stringify(args), new Error().stack.split("\n").slice(1, 8).join(" | "));
                return close.apply(this, args);
            };
        });
        page.on("console", (message) => { if (message.text().startsWith("ws.close")) log(message.text().slice(0, 900)); });
    }
    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable");
    if (!proxy) {
        const { label: _label, ...conditions } = PROFILES[profile];
        await cdp.send("Network.emulateNetworkConditions", { offline: false, ...conditions });
    }

    const resizes = [];
    page.on("request", (request) => {
        if (request.url().includes("/browser:display")) resizes.push(now());
    });
    const opened = now();
    const media = transport === "webrtc" ? await shapeTurn(target, profile) : null;
    await page.goto(`${frontend}/t/${target.pod_id}/computer?streamProbe=1&stream=${transport}`, { waitUntil: "domcontentloaded" });
    /* A fresh bench user is asked for a name first, and the dialog would take
       the clicks the typing scenario aims at the picture. */
    const skip = page.getByRole("button", { name: "Skip for now" });
    await skip.click({ timeout: 30_000 }).catch(() => undefined);
    await page.waitForFunction(() => window.__streamProbe?.live(), null, { timeout: 120_000, polling: 100 });
    const snap = await page.evaluate(() => window.__streamProbe.snapshot());
    const first = {
        navigation_to_first_frame_ms: round(snap.connect.firstFrameAt - opened, 0),
        socket_to_open_ms: snap.connect.openAt ? round(snap.connect.openAt - snap.connect.socketAt, 0) : null,
        socket_to_first_frame_ms: round(snap.connect.firstFrameAt - snap.connect.socketAt, 0),
        extensions: snap.connect.extensions,
        protocol: snap.connect.protocol,
    };
    const signalling = proxy ? "proxy" : shaped(PROFILES[profile]) ? "cdp" : "none";
    return { context, page, resizes, first, shaping: media ? `${signalling}; media ${media}` : signalling };
}

async function canvasCentre(page) {
    const box = await page.locator(".screen-surfaces canvas, .screen-video").last().boundingBox();
    if (!box) throw new Error("no canvas on the page");
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function runScenario(name, { page, sandbox, offset, resizes, quick }) {
    const seconds = quick ? 5 : 30;
    const mode = name === "resize" ? "scroll" : name;
    await sandbox.setMode(mode);
    await sleep(1500);
    await page.evaluate(() => window.__streamProbe.reset());
    const statsBefore = await sandbox.stats();
    const resizesBefore = resizes.length;
    const extra = {};

    if (name === "idle" || name === "animation") {
        await sleep(seconds * 1000);
    } else if (name === "scroll") {
        const centre = await canvasCentre(page);
        await page.mouse.move(centre.x, centre.y);
        const end = now() + seconds * 1000;
        let direction = 1;
        let flip = now() + 7500;
        while (now() < end) {
            await page.mouse.wheel(0, 120 * direction);
            await sleep(50);
            if (now() > flip) { direction = -direction; flip = now() + 7500; }
        }
    } else if (name === "typing") {
        const keys = quick ? 10 : 50;
        const centre = await canvasCentre(page);
        await page.mouse.click(centre.x, centre.y);
        await sleep(1000);
        await page.evaluate(() => window.__streamProbe.reset());
        const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
        for (let i = 0; i < keys; i++) {
            await page.keyboard.press(alphabet[i % alphabet.length]);
            await sleep(300);
        }
        await sleep(3000);
    } else if (name === "resize") {
        const times = quick ? 2 : 10;
        extra.resizes = [];
        for (let i = 0; i < times; i++) {
            const size = i % 2 === 0 ? RESIZED : VIEWPORT;
            const before = (await page.evaluate(() => window.__streamProbe.snapshot())).canvas.length;
            const at = now();
            await page.setViewportSize(size);
            let changed = null;
            let pictured = null;
            while (now() - at < 10_000) {
                const snap = await page.evaluate(() => window.__streamProbe.snapshot());
                const resized = snap.canvas.slice(before).at(-1);
                if (resized) {
                    changed ??= resized[0];
                    const after = snap.frames.find((f) => f[0] >= resized[0]);
                    if (after) { pictured = after[0]; break; }
                }
                await sleep(50);
            }
            extra.resizes.push({
                to: `${size.width}x${size.height}`,
                canvas_changed_ms: changed ? round(changed - at, 0) : null,
                picture_back_ms: pictured ? round(pictured - at, 0) : null,
            });
            await sleep(1500);
        }
    }

    const statsAfter = await sandbox.stats();
    const snap = await page.evaluate(() => window.__streamProbe.snapshot());
    return summarise(name, snap, { offset, cpu: cpuBetween(statsBefore, statsAfter), displayResizes: resizes.length - resizesBefore, extra });
}

function summarise(name, snap, { offset, cpu, displayResizes, extra }) {
    const seconds = (snap.now - snap.since) / 1000;
    const frames = snap.frames;
    /* A step backwards is a picture older than the one before it -- a
       region drawn late, or a strip read across two updates that parity let
       through -- not a 65,000-frame wrap. Counted, and left out of the sum. */
    let advanced = 0;
    let backwards = 0;
    for (let i = 1; i < frames.length; i++) {
        const step = (frames[i][2] - frames[i - 1][2] + 65536) % 65536;
        if (step > 32768) backwards++;
        else advanced += step;
    }
    const delivered = frames.length;
    const g2g = frames.map((f) => f[1] + offset);
    /* The longest the picture stood still, edges included: a scenario that
       stalls for 20 s and then catches up has a fine median and this. */
    const times = [snap.since, ...frames.map((f) => f[0]), snap.now];
    let longestGap = 0;
    for (let i = 1; i < times.length; i++) longestGap = Math.max(longestGap, times[i] - times[i - 1]);
    const dropped = snap.connections.filter(([, , , closedAt]) => closedAt !== null && closedAt >= snap.since && closedAt <= snap.now);

    const keys = {};
    if (name === "typing") {
        const echoes = snap.echoes;
        const echoLatency = [];
        const inputLatency = [];
        let coalesced = 0;
        let previous = 0;
        for (const [at, count, , inputRaw] of echoes) {
            const sent = snap.keysSent[count - 1];
            if (!sent) continue;
            if (count - previous > 1) coalesced += count - previous - 1;
            previous = count;
            echoLatency.push(at - sent[0]);
            inputLatency.push(inputRaw - offset);
        }
        Object.assign(keys, {
            sent: snap.keysSent.length,
            echoed: echoes.length ? echoes.at(-1)[1] : 0,
            seen_individually: echoLatency.length,
            coalesced,
            key_to_echo_ms: { p50: round(quantile(echoLatency, 0.5), 0), p95: round(quantile(echoLatency, 0.95), 0), max: round(quantile(echoLatency, 1), 0) },
            key_to_sandbox_ms: { p50: round(quantile(inputLatency, 0.5), 0), p95: round(quantile(inputLatency, 0.95), 0) },
        });
    }

    return {
        scenario: name,
        seconds: round(seconds, 1),
        fps_delivered: round(delivered / seconds, 1),
        sandbox_frames_advanced: advanced,
        frames_dropped: Math.max(0, advanced - (delivered - 1 - backwards)),
        drop_fraction: advanced > 0 ? round(Math.max(0, advanced - (delivered - 1 - backwards)) / advanced, 3) : null,
        frames_out_of_order: backwards,
        glass_to_glass_ms: {
            p50: round(quantile(g2g, 0.5), 0),
            p95: round(quantile(g2g, 0.95), 0),
            p99: round(quantile(g2g, 0.99), 0),
            max: round(quantile(g2g, 1), 0),
            samples: g2g.length,
        },
        longest_still_ms: round(longestGap, 0),
        socket_closes: dropped.length,
        close_codes: dropped.map(([, , , , code]) => code),
        keys: name === "typing" ? keys : undefined,
        bytes: {
            rx: snap.bytes.rx,
            tx: snap.bytes.tx,
            rx_kbps: round((snap.bytes.rx * 8) / seconds / 1000, 0),
            tx_kbps: round((snap.bytes.tx * 8) / seconds / 1000, 1),
            rx_messages: snap.bytes.rxMessages,
        },
        cpu,
        display_resize_requests: displayResizes,
        decode: snap.decode,
        /* Kept so a summary can be recomputed without running again. */
        raw: { frames: snap.frames, keysSent: snap.keysSent, echoes: snap.echoes, connections: snap.connections, canvas: snap.canvas },
        ...extra,
    };
}

/* ------------------------------------------------------------ the report */

function markdown(results) {
    const lines = [];
    const fmt = (v) => (v === null || v === undefined ? "–" : String(v));
    lines.push(`# Stream bench — ${results.target.name}`, "");
    lines.push(`${results.started} · git ${results.git} · sandbox ${results.target.sandbox} · ${results.quick ? "quick run" : "full run"}`, "");
    lines.push(`Clock offset (sandbox − viewer): ${results.clock.offset_ms} ms ± ${results.clock.error_ms} ms (best of ${results.clock.pings} pings, min RTT ${results.clock.rtt_ms.min} ms)`, "");
    lines.push("| transport | DPR | network | shaped by | first frame (nav → picture) | socket → first frame | permessage-deflate (browser ↔ API) |");
    lines.push("|---|---|---|---|---|---|---|");
    for (const run of results.runs) {
        lines.push(`| ${run.transport} | ${run.dpr} | ${PROFILES[run.profile].label} | ${fmt(run.shaping)} | ${fmt(run.first?.navigation_to_first_frame_ms)} ms | ${fmt(run.first?.socket_to_first_frame_ms)} ms | ${run.first ? (run.first.extensions || "not negotiated") : "–"} |`);
    }
    lines.push("");
    lines.push("| transport | scenario | DPR | network | fps delivered | sandbox fps | dropped | longest still ms | socket closes | g2g p50 / p95 ms | key→echo p50 / p95 ms | rx kbps | tx kbps | streaming cores | CPU vnc server / websockify / relay / streamer / Xvfb / chrome | display resizes |");
    lines.push("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|");
    for (const run of results.runs) {
        for (const s of run.scenarios) {
            if (s.error) { lines.push(`| ${run.transport} | ${s.scenario} | ${run.dpr} | ${PROFILES[run.profile].label} | error: ${s.error.split("\n")[0]} ||||||||||||`); continue; }
            const c = s.cpu.cores;
            lines.push([
                "", run.transport, s.scenario, run.dpr, PROFILES[run.profile].label, s.fps_delivered, fmt(s.cpu.page_paint_fps),
                s.drop_fraction === null ? "–" : `${s.frames_dropped} (${Math.round(s.drop_fraction * 100)}%)`,
                s.longest_still_ms, s.socket_closes ? `${s.socket_closes} (${s.close_codes.join(", ")})` : 0,
                `${fmt(s.glass_to_glass_ms.p50)} / ${fmt(s.glass_to_glass_ms.p95)}`,
                s.keys ? `${fmt(s.keys.key_to_echo_ms.p50)} / ${fmt(s.keys.key_to_echo_ms.p95)}` : "",
                s.bytes.rx_kbps, s.bytes.tx_kbps, fmt(s.cpu.streaming_cores),
                [c.tigervnc ?? c.x11vnc, c.websockify, c.relay, c.streamer, c.xvfb, c.chrome].map(fmt).join(" / "),
                s.display_resize_requests, "",
            ].join(" | ").trim());
        }
    }
    const resizes = results.runs.flatMap((run) => run.scenarios.filter((s) => s.resizes).map((s) => ({ run, s })));
    if (resizes.length) {
        lines.push("", "Resize: time from the viewport changing to the canvas taking the new size, and to the first decoded frame after it (ms).", "");
        for (const { run, s } of resizes) {
            lines.push(`- ${run.transport}, DPR ${run.dpr}, ${PROFILES[run.profile].label}: ` + s.resizes.map((r) => `${fmt(r.canvas_changed_ms)}/${fmt(r.picture_back_ms)}`).join(", "));
        }
    }
    return lines.join("\n") + "\n";
}

/* ------------------------------------------------------------ main */

async function main() {
    const args = parseArgs(process.argv.slice(2));
    const target = JSON.parse(readFileSync(args.target, "utf8"));
    const sandbox = probe(target);
    const started = new Date().toISOString();
    const dir = path.join(args.out, started.replace(/[:.]/g, "-"));
    mkdirSync(dir, { recursive: true });

    const clock = await clockOffset(sandbox);
    log(`clock offset ${clock.offset_ms} ms ± ${clock.error_ms} ms`);

    const frontend = args.frontend ? { origin: args.frontend, stop: () => {} } : await startFrontend(target);
    const browser = await chromium.launch({ headless: !args.headed });
    const git = await new Promise((resolve) => {
        const child = spawn("git", ["rev-parse", "--short", "HEAD"], { cwd: REPO });
        let out = "";
        child.stdout.on("data", (chunk) => { out += chunk; });
        child.on("close", () => resolve(out.trim()));
    });
    const results = { started, git, quick: Boolean(args.quick), target: { name: target.name, sandbox: target.sandbox }, clock, runs: [] };
    const save = () => {
        writeFileSync(path.join(dir, "results.json"), JSON.stringify(results, null, 2));
        writeFileSync(path.join(dir, "results.md"), markdown(results));
    };

    try {
        for (const transport of args.transports) for (const dpr of args.dpr) {
            for (const profile of args.profiles) {
                log(`${transport}, DPR ${dpr}, ${PROFILES[profile].label}`);
                const run = { transport, dpr, profile, scenarios: [] };
                results.runs.push(run);
                let viewer;
                try {
                    await sandbox.setMode("idle");
                    await sandbox.open();
                    viewer = await openViewer(browser, { frontend: frontend.origin, target, dpr, profile, shaper: args.shaper, transport });
                    run.first = viewer.first;
                    run.shaping = viewer.shaping;
                    log(`  first frame ${viewer.first.navigation_to_first_frame_ms} ms after navigation; extensions "${viewer.first.extensions}"`);
                    for (const name of args.scenarios) {
                        try {
                            const result = await runScenario(name, { page: viewer.page, sandbox, offset: clock.offset_ms, resizes: viewer.resizes, quick: args.quick });
                            run.scenarios.push(result);
                            log(`  ${name}: ${result.fps_delivered} fps, g2g p50 ${result.glass_to_glass_ms.p50} ms, ${result.bytes.rx_kbps} kbps, streaming CPU ${result.cpu.streaming_cores}`);
                        } catch (error) {
                            run.scenarios.push({ scenario: name, error: String(error?.stack ?? error) });
                            log(`  ${name}: failed: ${error}`);
                        }
                        save();
                    }
                    await viewer.page.setViewportSize(VIEWPORT);
                } catch (error) {
                    run.error = String(error?.stack ?? error);
                    log(`  failed: ${error}`);
                    if (viewer?.page) await viewer.page.screenshot({ path: path.join(dir, `failed-dpr${dpr}-${profile}.png`) }).catch(() => {});
                } finally {
                    await viewer?.context.close();
                    if (transport === "webrtc") await shapeTurn(target, "unthrottled");
                    save();
                }
            }
        }
    } finally {
        await sandbox.setMode("idle").catch(() => {});
        await browser.close();
        frontend.stop();
    }
    const legs = path.join(path.dirname(args.target), "legs.json");
    if (existsSync(legs)) results.legs = JSON.parse(readFileSync(legs, "utf8"));
    save();
    log(`wrote ${path.join(dir, "results.md")}`);
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
