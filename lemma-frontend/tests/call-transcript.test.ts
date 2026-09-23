import test from 'node:test';
import assert from 'node:assert/strict';
import { foldTranscript, isSameUtterance, type CallLine } from '../src/call/call-transcript.ts';

const fold = (chunks: Parameters<typeof foldTranscript>[1][]) =>
    chunks.reduce<CallLine[]>((lines, chunk) => foldTranscript(lines, chunk), []);

test('fragments from one speaker become one line, not one line each', () => {
    // Recognisers emit at their own rhythm, often mid-word. A line per
    // fragment would be a transcript one syllable wide.
    const lines = fold([
        { speaker: 'them', text: 'what were' },
        { speaker: 'them', text: ' the Q1' },
        { speaker: 'them', text: ' totals', final: true },
    ]);
    assert.equal(lines.length, 1);
    assert.equal(lines[0].text, 'what were the Q1 totals');
    assert.equal(lines[0].done, true);
});

test('the other speaker starts a new line even mid-sentence', () => {
    const lines = fold([
        { speaker: 'them', text: 'and the—' },
        { speaker: 'us', text: 'let me check' },
        { speaker: 'them', text: 'sorry, go on' },
    ]);
    assert.deepEqual(lines.map(l => [l.speaker, l.text]), [
        ['them', 'and the—'], ['us', 'let me check'], ['them', 'sorry, go on'],
    ]);
});

test('a closed line is never appended to', () => {
    const lines = fold([
        { speaker: 'us', text: 'done', final: true },
        { speaker: 'us', text: 'more' },
    ]);
    assert.equal(lines.length, 2);
    assert.equal(lines[0].text, 'done');
});

test('an empty close ends the line without adding a blank one', () => {
    // This is how a provider says "that speaker stopped". Treating it as a
    // line would put an empty row on screen every time somebody paused.
    const lines = fold([{ speaker: 'them', text: 'hello' }, { speaker: 'them', text: '', final: true }]);
    assert.equal(lines.length, 1);
    assert.equal(lines[0].done, true);
});

test('an empty fragment with nothing open is ignored entirely', () => {
    assert.deepEqual(fold([{ speaker: 'them', text: '   ' }]), []);
});

test('ids stay unique as the head is trimmed, so React keys never collide', () => {
    let lines: CallLine[] = [];
    for (let i = 0; i < 260; i++) {
        lines = foldTranscript(lines, { speaker: i % 2 ? 'them' : 'us', text: 'line ' + i, final: true });
    }
    // Captions read the last line or two; the cap is small because nothing
    // reads further back, and the record is the conversation on the server.
    assert.equal(lines.length, 24);
    assert.equal(new Set(lines.map(l => l.id)).size, 24);
    // Trimmed from the top: the newest survives.
    assert.equal(lines[lines.length - 1].text, 'line 259');
});

test('the same question asked again is recognised, punctuation and case aside', () => {
    // This is the guard that stopped one question appearing twice in the
    // person's own conversation: a fire-and-forget tool gives the voice no
    // answer to wait on, so it sometimes just asks again.
    assert.equal(isSameUtterance('What were the Q1 totals?', 'what were the q1 totals'), true);
    assert.equal(isSameUtterance('  the  Q1   totals ', 'the Q1 totals'), true);
});

test('a real follow-up is never mistaken for a repeat', () => {
    // Too loose here and a genuine second question is silently dropped, which
    // is a worse failure than the one this prevents.
    assert.equal(isSameUtterance('and Q2?', 'what were the Q1 totals'), false);
    assert.equal(isSameUtterance('the Q1 totals by vendor', 'the Q1 totals'), false);
    assert.equal(isSameUtterance('', 'the Q1 totals'), false);
    assert.equal(isSameUtterance('the Q1 totals', ''), false);
});
