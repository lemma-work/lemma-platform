import test from 'node:test';
import assert from 'node:assert/strict';
import { createPCMPlayer } from '../src/call/pcm-audio.ts';

test('interruption stops playing and queued buffers before scheduling replacement audio', context => {
    const sources: any[] = [];
    let closed = 0;
    class FakeContext {
        currentTime = 10;
        destination = {};
        createAnalyser() { return {connect() {}, disconnect() {}, frequencyBinCount:128, getByteTimeDomainData() {}}; }
        createBuffer(_channels: number, length: number, rate: number) { return {duration:length/rate,copyToChannel() {}}; }
        createBufferSource() {
            const source = {buffer:null,onended:null,when:0,stops:0,disconnections:0,connect() {},start(when:number) {this.when=when;},stop() {this.stops++;},disconnect() {this.disconnections++;}};
            sources.push(source); return source;
        }
        close() {closed++; return Promise.resolve();}
    }
    const previous = Object.getOwnPropertyDescriptor(globalThis, 'AudioContext');
    Object.defineProperty(globalThis, 'AudioContext', {value:FakeContext,configurable:true});
    context.after(() => {if(previous)Object.defineProperty(globalThis,'AudioContext',previous);else Reflect.deleteProperty(globalThis,'AudioContext');});
    const player = createPCMPlayer();
    const chunk = Buffer.alloc(4800).toString('base64');
    player.push(chunk); player.push(chunk);
    assert.equal(sources[0].when, 10);
    assert.equal(sources[1].when, 10.1);
    player.clear();
    assert.deepEqual(sources.map(s => s.stops), [1,1]);
    assert.deepEqual(sources.map(s => s.disconnections), [1,1]);
    player.push(chunk);
    assert.equal(sources[2].when, 10);
    player.stop(); player.stop(); player.push(chunk);
    assert.equal(sources[2].stops, 1);
    assert.equal(sources.length, 3);
    assert.equal(closed, 1);
});
