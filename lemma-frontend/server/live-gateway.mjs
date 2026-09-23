import { randomUUID } from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';

/* Same job as `voice-gateway`, different model. GPT-Live runs in client
   delegation mode, which means it holds the microphone and the floor and
   nothing else: when it needs real work done it opens a delegation and waits
   for this pod to answer. There is no backend model configured on the
   session at all — that slot is the teammate, and it stays where it is. */

/* Named for the same reason the Gemini voice is named: the face on screen and
   the voice in the pod have to be the same person, and a default is a coin
   flip against the avatar. `marin` is GPT-Live's own default. Immutable once
   the session starts, so it is read here rather than per-call. */
const VOICE = process.env.OPENAI_LIVE_VOICE || 'marin';
const MODEL = process.env.OPENAI_LIVE_MODEL || 'gpt-live-1';
/* Configurable because it is the one part of this that could not be checked
   against a live session before it was written. */
const ENDPOINT = process.env.OPENAI_LIVE_URL || 'wss://api.openai.com/v1/live/sessions';

/* What the browser is allowed to hear, and the only fields of it that get
   through. Everything else GPT-Live emits — acknowledgments, usage, session
   bookkeeping — stops at this server, the same way Gemini's raw messages do,
   and a field the provider adds later does not reach the browser by default. */
const RELAYED = new Set([
    'session.output_audio.delta',
    'session.input_transcript.delta',
    'session.output_transcript.delta',
    'session.delegation.created',
]);

const APPENDS = new Set(['instructions', 'thinking', 'commentary']);

function relayable(event) {
    if (!event || !RELAYED.has(event.type)) return null;
    const relayed = { type: event.type };
    for (const field of ['delta', 'audio', 'start_ms', 'end_ms', 'offset_ms']) {
        if (event[field] !== undefined) relayed[field] = event[field];
    }
    if (event.delegation) relayed.delegation = { id: event.delegation.id, target: event.delegation.target };
    /* Spoken audio arrives in `delta` — the same field name the two transcript
       events use for text. It is moved to `audio` here so that one field does
       not mean two things by the time the browser sees it, and so the thing
       that decides "is this sound or is this words" is the event type. */
    if (event.type === 'session.output_audio.delta' && typeof relayed.delta === 'string' && relayed.audio === undefined) {
        relayed.audio = relayed.delta;
        delete relayed.delta;
    }
    return relayed;
}

/* Set LIVE_DEBUG=1 to print what a call actually consisted of. Written
   because "I can't hear anything" has at least four causes that look
   identical from the browser — the model never spoke, it spoke and the audio
   was dropped, it never heard you, or it heard you and never delegated — and
   the event tally tells them apart in one call.

   On in dev without asking, because a flag you have to remember is a flag you
   find out you forgot one call later. LIVE_DEBUG=0 turns it off; LIVE_DEBUG=1
   turns it on anywhere. */
const DEBUG = process.env.LIVE_DEBUG === '1'
    || (process.env.LIVE_DEBUG !== '0' && process.argv.includes('--dev'));

/** Decoded size and loudest sample of a base64 PCM16 frame. A frame of pure
 *  zeros is digital silence, which is the difference between "nothing is
 *  playing" and "nothing is being said". */
function peakOf(base64) {
    const bytes = Buffer.from(base64, 'base64');
    let peak = 0;
    for (let i = 0; i + 1 < bytes.length; i += 2) peak = Math.max(peak, Math.abs(bytes.readInt16LE(i)));
    return { bytes: bytes.length, peak };
}

function tally() {
    const events = new Map();
    const audio = { in: { frames: 0, bytes: 0, peak: 0 }, out: { frames: 0, bytes: 0, peak: 0, silent: 0 } };
    return {
        event(type) { events.set(type, (events.get(type) ?? 0) + 1); },
        frame(direction, base64) {
            const { bytes, peak } = peakOf(base64);
            const side = audio[direction];
            side.frames++; side.bytes += bytes; side.peak = Math.max(side.peak, peak);
            if (direction === 'out' && peak === 0) side.silent++;
        },
        report(label) {
            if (!DEBUG) return;
            const seen = [...events.entries()].map(([type, count]) => `${type} x${count}`).join(', ') || 'none';
            console.log(`[live ${label}] events: ${seen}`);
            console.log(`[live ${label}] mic -> ${audio.in.frames} frames, ${audio.in.bytes}B, peak ${audio.in.peak}`);
            console.log(`[live ${label}] voice <- ${audio.out.frames} frames, ${audio.out.bytes}B, peak ${audio.out.peak}, ${audio.out.silent} silent`);
        },
    };
}

function openLiveSession({ instructions, onEvent, onError, onClose }) {
    if (!process.env.OPENAI_API_KEY) throw new Error('missing key');
    const socket = new WebSocket(ENDPOINT, { headers: { Authorization: `Bearer ${process.env.OPENAI_API_KEY}` } });
    return new Promise((resolve, reject) => {
        let started = false;
        socket.on('open', () => socket.send(JSON.stringify({
            type: 'session.start',
            event_id: randomUUID(),
            session: {
                model: MODEL,
                instructions,
                audio: { output: { voice: VOICE } },
                /* Client delegation: the work comes back here, not to a model
                   OpenAI picked. Omitting this selects it anyway; it is
                   written out because it is the whole point of the file. */
                delegation: { type: 'client' },
            },
        })));
        socket.on('message', data => {
            let event;
            try { event = JSON.parse(data.toString()); } catch { return; }
            if (!started) {
                if (event.type === 'session.started') {
                    started = true;
                    resolve({ send: value => { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value)); }, close: () => socket.close() });
                    return;
                }
                if (event.type === 'error') { reject(new Error('session rejected')); socket.close(); }
                return;
            }
            if (event.type === 'error') { onError(event.error ?? event); return; }
            onEvent(event);
        });
        socket.on('error', () => { if (!started) reject(new Error('connection failed')); else onError(); });
        socket.on('close', () => { if (!started) reject(new Error('closed before ready')); else onClose(); });
    });
}

export function attachLiveGateway(server, { connect, maxCalls = 8, maxDuration = 15 * 60_000 } = {}) {
    const wss = new WebSocketServer({ noServer: true, maxPayload: 256 * 1024 });
    server.on('upgrade', (request, socket, head) => {
        if (new URL(request.url, 'http://localhost').pathname !== '/api/live') return;
        if (wss.clients.size >= maxCalls) {
            socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');
            return;
        }
        wss.handleUpgrade(request, socket, head, ws => wss.emit('connection', ws));
    });
    wss.on('connection', ws => {
        const counts = tally();
        let session;
        let started = false;
        let closed = false;
        let alive = true;
        const send = value => {
            if (ws.readyState !== WebSocket.OPEN) return;
            if (ws.bufferedAmount > 1024 * 1024) { ws.close(1013, 'Connection too slow'); return; }
            ws.send(JSON.stringify(value));
        };
        const fail = message => { send({ type: 'error', message }); ws.close(1011, 'Voice session ended'); };
        const startup = setTimeout(() => fail('Voice setup timed out.'), 20000);
        const duration = setTimeout(() => ws.close(1000, 'Call time limit reached'), maxDuration);
        const heartbeat = setInterval(() => {
            if (!alive) { ws.terminate(); return; }
            alive = false;
            ws.ping();
        }, 30000);
        ws.on('pong', () => { alive = true; });
        /* A ticker rather than only a summary on hangup: the question being
           asked is usually "is anything happening right now", and waiting for
           the call to end to find out is the slow way to learn it isn't. */
        const ticker = DEBUG ? setInterval(() => counts.report('live'), 3000) : null;
        const cleanup = () => {
            closed = true;
            counts.report('ended');
            if (ticker) clearInterval(ticker);
            clearTimeout(startup); clearTimeout(duration); clearInterval(heartbeat);
            session?.close(); session = undefined;
        };
        ws.on('close', cleanup);
        ws.on('error', cleanup);
        ws.on('message', async data => {
            try {
                const { type, payload } = JSON.parse(data.toString());
                if (type === 'start') {
                    if (started) throw new Error('duplicate start');
                    started = true;
                    /* GPT-Live caps instructions at 16,384 tokens and freezes
                       them at startup; more can only arrive as appends. */
                    if (typeof payload?.instructions !== 'string' || payload.instructions.length > 16000) throw new Error('invalid setup');
                    if (!connect && !process.env.OPENAI_API_KEY) { fail('Voice is not configured on this server.'); return; }
                    const establish = connect ?? openLiveSession;
                    const opened = await establish({
                        instructions: payload.instructions,
                        onEvent: event => {
                            counts.event(event.type);
                            const payload = relayable(event);
                            if (!payload) return;
                            if (typeof payload.audio === 'string') counts.frame('out', payload.audio);
                            send({ type: 'event', payload });
                        },
                        onError: detail => {
                            /* The provider's own words, to this server's log
                               only — they are the one place an unknown field
                               or a rejected session says so by name. */
                            if (DEBUG) console.error('[live error]', JSON.stringify(detail));
                            fail('GPT-Live connection failed.');
                        },
                        onClose: () => ws.close(1000, 'Voice session ended'),
                    });
                    if (closed || ws.readyState !== WebSocket.OPEN) { opened.close(); return; }
                    session = opened;
                    clearTimeout(startup);
                    send({ type: 'ready' });
                    return;
                }
                if (!session) throw new Error('not ready');
                if (type === 'audio') {
                    const audio = payload?.audio;
                    /* Raw headerless mono PCM16LE at 24kHz, base64'd. Two
                       bytes to the sample, so an odd byte count is malformed
                       by definition — base64 length divisible by 4 and no
                       single trailing pad byte is the cheap version of that
                       check. */
                    if (typeof audio !== 'string' || audio.length > 64000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(audio)) throw new Error('invalid audio');
                    if (DEBUG) counts.frame('in', audio);
                    session.send({ type: 'session.input_audio.append', event_id: randomUUID(), audio });
                } else if (type === 'append') {
                    const { kind, delegation_id: delegation, content } = payload ?? {};
                    if (!APPENDS.has(kind)) throw new Error('invalid append');
                    if (typeof content !== 'string' || !content || content.length > 6000) throw new Error('invalid append');
                    if (delegation !== null && (typeof delegation !== 'string' || delegation.length > 200)) throw new Error('invalid append');
                    if (DEBUG) console.log(`[live append] ${kind} -> ${delegation ?? 'session'}: ${content.slice(0, 120)}`);
                    session.send({ type: `session.${kind}.append`, event_id: randomUUID(), delegation_id: delegation, content });
                } else if (type === 'close') {
                    session.send({ type: 'session.close', event_id: randomUUID() });
                } else throw new Error('unknown message');
            } catch { fail('Could not process the voice session. Please try again.'); }
        });
    });
    return wss;
}
