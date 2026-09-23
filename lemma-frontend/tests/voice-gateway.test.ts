import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { WebSocket } from 'ws';
import { attachVoiceGateway, thinkingFor } from '../server/voice-gateway.mjs';

test('unauthenticated socket relays audio without tools, fixes model, and cleans up Gemini', async () => {
    const server = createServer();
    let callbacks: any;
    const received: any[] = [];
    let closed = false;
    let finish!: () => void;
    const providerClosed = new Promise<void>(resolve => { finish = resolve; });
    const gateway = attachVoiceGateway(server, {connect: async (options: any) => {
        assert.equal(options.model, 'gemini-3.8-live');
        // The lighter model rejects a thinking level outright, so the config
        // must not carry one — not an empty one, not undefined, absent.
        assert.equal('thinkingConfig' in options.config, false);
        // Both directions, because the call screen shows what was said.
        assert.deepEqual(options.config.inputAudioTranscription, {});
        assert.deepEqual(options.config.outputAudioTranscription, {});
        // All routing belongs to Jev; the realtime session advertises no tools.
        assert.equal('tools' in options.config, false);
        callbacks = options.callbacks;
        return {sendRealtimeInput: (v: any) => received.push(v), sendClientContent: (v: any) => received.push(v), sendToolResponse: (v: any) => received.push(v), close: () => {closed = true; finish();} };
    }});
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const address = server.address() as {port:number};
    const ws = new WebSocket(`ws://127.0.0.1:${address.port}/api/voice`);
    try {
        await once(ws, 'open');
        let message = once(ws, 'message');
        ws.send(JSON.stringify({type:'start',payload:{systemInstruction:'Test'}}));
        assert.equal(JSON.parse(String((await message)[0])).type, 'ready');
        message = once(ws, 'message');
        callbacks.onmessage({serverContent:{interrupted:true, interactionStatus:'IN_PROGRESS'}, secret:'must not relay'});
        const event = JSON.parse(String((await message)[0]));
        assert.equal(event.payload.serverContent.interrupted, true);
        // The signal that replaced turnComplete as "is it done": the browser
        // cannot tell reasoning from a dead call without it reaching them.
        assert.equal(event.payload.serverContent.interactionStatus, 'IN_PROGRESS');
        assert.equal(event.payload.secret, undefined);
        ws.send(JSON.stringify({type:'audio',payload:{audio:{data:'AAAA',mimeType:'untrusted'}}}));
        ws.send(JSON.stringify({type:'content',payload:{turns:'context',turnComplete:false}}));
        // A pong establishes that the server processed the preceding frames.
        const pong = once(ws, 'pong'); ws.ping(); await pong;
        assert.deepEqual(received[0], {audio:{data:'AAAA',mimeType:'audio/pcm;rate=16000'}});
        assert.deepEqual(received[1], { turns: [{ role: 'model', parts: [{ text: 'context' }] }], turnComplete: false });
        ws.send(JSON.stringify({type:'content',payload:{turns:'Ask which report',turnComplete:true}}));
        const spokenPong = once(ws, 'pong'); ws.ping(); await spokenPong;
        assert.deepEqual(received[2], { turns: [{ role: 'user', parts: [{ text: 'Ask which report' }] }], turnComplete: true });
        const end = once(ws, 'close'); ws.close(); await end;
        await providerClosed;
        assert.equal(closed, true);
    } finally { ws.terminate(); gateway.close(); server.close(); }
});

test('a thinking level is attached to the model that takes one and no other', () => {
    // Two models, one rule. gemini-3.8-live rejects a thinkingLevel outright
    // — "Thinking level is not supported for this model", closed mid-setup,
    // checked against the real API — and the extended-thinking variant is the
    // only place the knob means anything. The model name is what decides.
    assert.deepEqual(thinkingFor('gemini-3.8-live-extended-thinking'), {thinkingLevel: 'LOW'});
    assert.equal(thinkingFor('gemini-3.8-live'), undefined);
});

test('provider failures return a sanitized error and close the socket', async () => {
    const server = createServer();
    const gateway = attachVoiceGateway(server, {connect: async () => {throw new Error('SECRET_KEY');}});
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const ws = new WebSocket(`ws://127.0.0.1:${(server.address() as {port:number}).port}/api/voice`);
    try {
        await once(ws, 'open'); const message = once(ws, 'message');
        ws.send(JSON.stringify({type:'start',payload:{systemInstruction:'Test'}}));
        const text = String((await message)[0]);
        assert.equal(JSON.parse(text).type, 'error'); assert.doesNotMatch(text, /SECRET_KEY/);
        await once(ws, 'close');
    } finally {ws.terminate(); gateway.close(); server.close();}
});
