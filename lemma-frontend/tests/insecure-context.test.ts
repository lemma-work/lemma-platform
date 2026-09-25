import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { sha256, digestSha256 } from "../src/auth/sha256.ts";
import { solve, type Challenge } from "../src/auth/altcha.ts";
import { newId } from "../src/call/ids.ts";

/** A Desktop installation shared on the local network is plain HTTP at a
 *  private address: not a secure context, so no `crypto.subtle` and no
 *  `crypto.randomUUID`. What depended on them has to work without. */

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const text = (value: string) => new TextEncoder().encode(value);

test("the script SHA-256 matches the FIPS 180-4 vectors", () => {
    assert.equal(hex(sha256(text(""))), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert.equal(hex(sha256(text("abc"))), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    assert.equal(
        hex(sha256(text("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"))),
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    );
});

test("the script SHA-256 agrees with crypto.subtle across block boundaries", async () => {
    for (const length of [0, 1, 55, 56, 63, 64, 65, 119, 120, 1000]) {
        const message = new Uint8Array(length).map((_, i) => (i * 31 + 7) & 0xff);
        const native = new Uint8Array(await webcrypto.subtle.digest("SHA-256", message));
        assert.equal(hex(sha256(message)), hex(native), `length ${length}`);
    }
});

async function challenge(salt: string, number: number): Promise<Challenge> {
    return {
        enabled: true,
        algorithm: "SHA-256",
        challenge: hex(sha256(text(salt + number))),
        maxnumber: 5000,
        salt,
        signature: "sig",
    };
}

test("the proof of work is found without crypto.subtle", async () => {
    const proof = await solve(await challenge("lan", 1234), async (message) => sha256(message));
    assert.ok(proof);
    const answer = JSON.parse(Buffer.from(proof!.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString());
    assert.equal(answer.number, 1234);
});

test("digestSha256 falls back when the page has no crypto.subtle", async () => {
    const saved = Object.getOwnPropertyDescriptor(globalThis, "crypto");
    Object.defineProperty(globalThis, "crypto", { value: { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) }, configurable: true });
    try {
        assert.equal(hex(await digestSha256(text("abc"))), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        const id = newId();
        assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
        assert.notEqual(newId(), id);
    } finally {
        if (saved) Object.defineProperty(globalThis, "crypto", saved);
    }
});
