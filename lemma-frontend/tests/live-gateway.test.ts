import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { WebSocket } from 'ws';
import { attachLiveGateway } from '../server/live-gateway.mjs';

/** Open a gateway on a loose port and a browser socket into it. */
async function open(options: any) {
    const server = createServer();
    const gateway = attachLiveGateway(server, options);
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const { port } = server.address() as { port: number };
    const ws = new WebSocket(`ws://127.0.0.1:${port}/api/live`);
    await once(ws, 'open');
    return { ws, done: () => { ws.terminate(); gateway.close(); server.close(); } };
}

test('a socket relays audio and appends, and cleans up GPT-Live', async () => {
    let handlers: any;
    const sent: any[] = [];
    let closed = false;
    let finish!: () => void;
    const providerClosed = new Promise<void>(resolve => { finish = resolve; });
    const { ws, done } = await open({ connect: async (options: any) => {
        assert.equal(options.instructions, 'Test');
        handlers = options;
        return { send: (value: any) => sent.push(value), close: () => { closed = true; finish(); } };
    }});
    try {
        let message = once(ws, 'message');
        ws.send(JSON.stringify({ type: 'start', payload: { instructions: 'Test' } }));
        assert.equal(JSON.parse(String((await message)[0])).type, 'ready');

        message = once(ws, 'message');
        handlers.onEvent({ type: 'session.delegation.created', offset_ms: 1000, delegation: { id: 'item_1', target: 'client', extra: 'x' }, usage: { secret: 1 } });
        const event = JSON.parse(String((await message)[0]));
        assert.equal(event.payload.type, 'session.delegation.created');
        assert.equal(event.payload.delegation.id, 'item_1');
        assert.equal(event.payload.offset_ms, 1000);
        // Only the named fields cross: bookkeeping the provider adds stays here.
        assert.equal(event.payload.usage, undefined);
        assert.equal(event.payload.delegation.extra, undefined);

        ws.send(JSON.stringify({ type: 'audio', payload: { audio: 'AAAA' } }));
        ws.send(JSON.stringify({ type: 'append', payload: { kind: 'commentary', delegation_id: 'item_1', content: 'It is done.' } }));
        ws.send(JSON.stringify({ type: 'append', payload: { kind: 'thinking', delegation_id: null, content: 'Session context.' } }));
        const pong = once(ws, 'pong'); ws.ping(); await pong;

        assert.equal(sent[0].type, 'session.input_audio.append');
        assert.equal(sent[0].audio, 'AAAA');
        assert.ok(sent[0].event_id, 'every client event carries an id');
        assert.equal(sent[1].type, 'session.commentary.append');
        assert.equal(sent[1].delegation_id, 'item_1');
        assert.equal(sent[1].content, 'It is done.');
        // null is the documented way to say "this is for the session, not a task".
        assert.equal(sent[2].type, 'session.thinking.append');
        assert.equal(sent[2].delegation_id, null);

        const end = once(ws, 'close'); ws.close(); await end;
        await providerClosed;
        assert.equal(closed, true);
    } finally { done(); }
});

test('an event the browser has no business seeing is not relayed', async () => {
    let handlers: any;
    const { ws, done } = await open({ connect: async (options: any) => {
        handlers = options;
        return { send: () => {}, close: () => {} };
    }});
    try {
        let message = once(ws, 'message');
        ws.send(JSON.stringify({ type: 'start', payload: { instructions: 'Test' } }));
        await message;

        message = once(ws, 'message');
        handlers.onEvent({ type: 'session.commentary.appended', client_event_id: 'e1' });
        handlers.onEvent({ type: 'session.closed', usage: { total: 900 } });
        handlers.onEvent({ type: 'session.output_audio.delta', audio: 'BBBB', start_ms: 10, end_ms: 20 });
        const relayed = JSON.parse(String((await message)[0]));
        assert.equal(relayed.payload.type, 'session.output_audio.delta');
        assert.equal(relayed.payload.audio, 'BBBB');
    } finally { done(); }
});

test('spoken audio reaches the browser as audio, whichever field it arrived in', async () => {
    let handlers: any;
    const { ws, done } = await open({ connect: async (options: any) => {
        handlers = options;
        return { send: () => {}, close: () => {} };
    }});
    try {
        let message = once(ws, 'message');
        ws.send(JSON.stringify({ type: 'start', payload: { instructions: 'Test' } }));
        await message;

        /* What a real session sends: the PCM is in `delta`, the same field
           name `session.input_transcript.delta` uses for text. Reading it as
           `audio` in the browser is how a call arrives and plays nothing. */
        message = once(ws, 'message');
        handlers.onEvent({ type: 'session.output_audio.delta', delta: 'CCCC' });
        const spoken = JSON.parse(String((await message)[0]));
        assert.equal(spoken.payload.audio, 'CCCC');
        assert.equal(spoken.payload.delta, undefined);

        /* Transcript text keeps its own field — the move is for audio only. */
        message = once(ws, 'message');
        handlers.onEvent({ type: 'session.input_transcript.delta', delta: 'what is', start_ms: 0, end_ms: 200 });
        const heard = JSON.parse(String((await message)[0]));
        assert.equal(heard.payload.delta, 'what is');
        assert.equal(heard.payload.audio, undefined);
    } finally { done(); }
});

test('malformed audio and unknown appends end the session rather than reaching the model', async () => {
    for (const bad of [
        { type: 'audio', payload: { audio: 'not base64!!' } },
        { type: 'append', payload: { kind: 'speak', delegation_id: null, content: 'hi' } },
        { type: 'append', payload: { kind: 'commentary', delegation_id: null, content: 'x'.repeat(6001) } },
        { type: 'append', payload: { kind: 'commentary', delegation_id: { id: 1 }, content: 'hi' } },
    ]) {
        const sent: any[] = [];
        const { ws, done } = await open({ connect: async () => ({ send: (v: any) => sent.push(v), close: () => {} }) });
        try {
            const ready = once(ws, 'message');
            ws.send(JSON.stringify({ type: 'start', payload: { instructions: 'Test' } }));
            await ready;

            const message = once(ws, 'message');
            ws.send(JSON.stringify(bad));
            assert.equal(JSON.parse(String((await message)[0])).type, 'error', JSON.stringify(bad));
            await once(ws, 'close');
            assert.deepEqual(sent, [], JSON.stringify(bad));
        } finally { done(); }
    }
});

test('provider failures return a sanitized error and close the socket', async () => {
    const { ws, done } = await open({ connect: async () => { throw new Error('OPENAI_SECRET'); } });
    try {
        const message = once(ws, 'message');
        ws.send(JSON.stringify({ type: 'start', payload: { instructions: 'Test' } }));
        const text = String((await message)[0]);
        assert.equal(JSON.parse(text).type, 'error');
        assert.doesNotMatch(text, /OPENAI_SECRET/);
        await once(ws, 'close');
    } finally { done(); }
});

test('oversized instructions never reach the provider', async () => {
    let reached = false;
    const { ws, done } = await open({ connect: async () => { reached = true; return { send: () => {}, close: () => {} }; } });
    try {
        const message = once(ws, 'message');
        ws.send(JSON.stringify({ type: 'start', payload: { instructions: 'x'.repeat(16001) } }));
        assert.equal(JSON.parse(String((await message)[0])).type, 'error');
        await once(ws, 'close');
        assert.equal(reached, false);
    } finally { done(); }
});
