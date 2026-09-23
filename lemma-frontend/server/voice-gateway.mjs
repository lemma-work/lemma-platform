import { GoogleGenAI, Modality, ThinkingLevel } from '@google/genai';
import { WebSocketServer, WebSocket } from 'ws';

/* Gemini 3.8 Live. The voice on this call does not think for itself — it
   listens, hands the work to the teammate, and says what comes back — so the
   extended-thinking variant was paying latency for reasoning that belongs to
   the agent behind it.

   One thing goes with it: `gemini-3.8-live` never sends `interactionStatus`
   (checked against a live session, not assumed), so the model no longer says
   when it is still busy. The call reads the teammate's own run for that
   instead, which is the better signal anyway — see `use-huddle`.

   Overridable, and `gemini-3.8-live-extended-thinking` is the other end of
   it: the same session with a thinking level attached. */
const MODEL = process.env.GEMINI_LIVE_MODEL || 'gemini-3.8-live';

/* LOW is where the docs start, and the ceiling here is conversational, not
   analytical — the hard thinking is the teammate's job and always was.
   Uppercased on the way through because the SDK hands `thinkingConfig` to the
   wire untransformed, so whatever is written here is what the server reads.
   It takes either case — both were opened against a live session — so this is
   only so that an env var set in any spelling arrives as `ThinkingLevel`'s. */
const THINKING_LEVEL = (process.env.GEMINI_THINKING_LEVEL || ThinkingLevel.LOW).toUpperCase();

/** The thinking config for a model, or nothing for a model that refuses one.
 *
 *  Not a style choice: `thinkingLevel` must be omitted for `gemini-3.8-live`
 *  and is required to mean anything on the extended-thinking variant, so the
 *  model name is what decides. `includeThoughts` stays off — thought
 *  summaries would be a second, written copy of the thing this model already
 *  says out loud, and nothing in this app reads them. */
export function thinkingFor(model) {
    return model.includes('extended-thinking') ? { thinkingLevel: THINKING_LEVEL } : undefined;
}

export function attachVoiceGateway(server, { connect, maxCalls = 8, maxDuration = 15 * 60_000 } = {}) {
    const wss = new WebSocketServer({ noServer: true, maxPayload: 256 * 1024 });
    server.on('upgrade', (request, socket, head) => {
        if (new URL(request.url, 'http://localhost').pathname !== '/api/voice') return;
        if (wss.clients.size >= maxCalls) {
            socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');
            return;
        }
        wss.handleUpgrade(request, socket, head, ws => wss.emit('connection', ws));
    });
    wss.on('connection', ws => {
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
        const cleanup = () => {
            closed = true;
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
                    if (typeof payload?.systemInstruction !== 'string' || payload.systemInstruction.length > 16000) throw new Error('invalid setup');
                    if (!connect && !process.env.GEMINI_API_KEY) { fail('Voice is not configured on this server.'); return; }
                    /* No `apiVersion` pin any more. v1alpha was there for a
                       preview model; 3.8 Live is stable and the SDK's own
                       default (v1beta) is the version Google documents it
                       against, so naming one here could only ever be wrong. */
                    const establish = connect ?? (options => new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY }).live.connect(options));
                    const thinkingConfig = thinkingFor(MODEL);
                    const opened = await establish({
                        model: MODEL,
                        config: {
                            responseModalities: [Modality.AUDIO],
                            systemInstruction: payload.systemInstruction,
                            // Jev routes committed transcripts; the voice has no tools.
                            /* Both directions, because the call shows what was
                               said. Empty config is deliberate: the language is
                               detected rather than declared, and a hint here
                               would be this server guessing at who is on the
                               call. */
                            inputAudioTranscription: {},
                            outputAudioTranscription: {},
                            ...(thinkingConfig ? { thinkingConfig } : {}),
                        },
                        callbacks: {
                            onmessage: message => send({ type: 'event', payload: { serverContent: message.serverContent } }),
                            onerror: () => fail('Gemini voice connection failed.'),
                            onclose: event => {
                                const code = event?.code ?? 1006;
                                send({ type: 'error', message: `Voice provider disconnected (code ${code}). Please reconnect the call.` });
                                ws.close(1011, 'Voice provider disconnected');
                            },
                        },
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
                    if (typeof audio?.data !== 'string' || audio.data.length > 64000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(audio.data)) throw new Error('invalid audio');
                    session.sendRealtimeInput({ audio: { data: audio.data, mimeType: 'audio/pcm;rate=16000' } });
                } else if (type === 'content') {
                    if (typeof payload?.turns !== 'string' || payload.turns.length > 64000 || typeof payload.turnComplete !== 'boolean') throw new Error('invalid context');
                    if (payload.turnComplete) {
                            session.sendClientContent({ turns: [{ role: 'user', parts: [{ text: payload.turns }] }], turnComplete: true });
                    } else {
                        // Quiet history is appended without requesting speech.
                        session.sendClientContent({ turns: [{ role: 'model', parts: [{ text: payload.turns }] }], turnComplete: false });
                    }
                } else throw new Error('unknown message');
            } catch { fail('Could not process the voice session. Please try again.'); }
        });
    });
    return wss;
}
