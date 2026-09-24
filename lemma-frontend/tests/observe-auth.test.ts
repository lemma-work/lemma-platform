import test from "node:test";
import assert from "node:assert/strict";
import type { AuthState } from "lemma-sdk";
import { observeAuth } from "../src/session/observe-auth.ts";

const out: AuthState = { status: "unauthenticated", user: null };
const signedIn: AuthState = { status: "authenticated", user: { id: "person", email: "person@example.test" } };
function source(initial = out, isTokenMode = false) {
    let state = initial;
    const listeners = new Set<(next: AuthState) => void>();
    return {
        isTokenMode,
        getState: () => state,
        subscribe: (listener: (next: AuthState) => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
        checkAuth: async () => state,
        emit: (next: AuthState) => { state = next; listeners.forEach(listener => listener(next)); },
    };
}
const settle = () => new Promise<void>(resolve => queueMicrotask(() => queueMicrotask(resolve)));

test("a rejected bearer token never falls back to another cookie identity", async () => {
    let probes = 0;
    const states: AuthState[] = [];
    const stop = observeAuth(source(out, true), async () => { probes++; return signedIn.user; }, state => states.push(state));
    await settle();
    assert.equal(probes, 0);
    assert.deepEqual(states, [out]);
    stop();
});

test("startup discovers cookies even when the SDK has already settled", async () => {
    const states: AuthState[] = [];
    const stop = observeAuth(source(), async () => signedIn.user, state => states.push(state));
    await settle();
    assert.deepEqual(states, [{ status: "loading", user: null }, signedIn]);
    stop();
});

test("a late cookie probe cannot overwrite session invalidation or a new identity", async () => {
    for (const next of [out, { ...signedIn, user: { id: "new-person", email: "new@example.test" } }]) {
        const auth = source();
        let resolve!: (user: typeof signedIn.user) => void;
        const states: AuthState[] = [];
        const stop = observeAuth(auth, () => new Promise(done => { resolve = done; }), state => states.push(state));
        auth.emit(next);
        resolve(signedIn.user);
        await settle();
        assert.deepEqual(states.at(-1), next);
        stop();
    }
});

test("session expiry after a successful check does not start another cookie probe", () => {
    const auth = source(signedIn);
    let probes = 0;
    const states: AuthState[] = [];
    const stop = observeAuth(auth, async () => { probes++; return signedIn.user; }, state => states.push(state));
    auth.emit({ status: "loading", user: null });
    auth.emit(signedIn);
    auth.emit(out);
    assert.equal(probes, 0);
    assert.deepEqual(states.at(-1), out);
    stop();
});

test("unmount discards pending session discovery", async () => {
    let resolve!: (user: typeof signedIn.user) => void;
    const states: AuthState[] = [];
    const stop = observeAuth(source(), () => new Promise(done => { resolve = done; }), state => states.push(state));
    stop();
    resolve(signedIn.user);
    await settle();
    assert.equal(states.length, 1);
});
