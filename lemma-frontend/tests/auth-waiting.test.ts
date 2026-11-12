import test from "node:test";
import assert from "node:assert/strict";
import { sayableName, waitingFor } from "../src/auth/waiting.ts";

/** What a sign-in page can honestly say about where it is sending somebody. */

test("a destination that names a teammate names them", () => {
    const waiting = waitingFor("/t/marketing/library");
    assert.equal(waiting.name, "Marketing");
    assert.equal(waiting.faces.length, 1, "one door, one face");
});

test("the app's own paths name the Lemma they are inside, and the app's places name nobody", () => {
    assert.equal(waitingFor("/t/outcome/marketing/apps").name, "Marketing");
    assert.equal(waitingFor("/t/chats/marketing/c/c1").name, "Marketing");
    assert.equal(waitingFor("/t/outcome/marketing").faces[0], waitingFor("/t/marketing/library").faces[0], "the same face either way");
    assert.equal(waitingFor("/t/needs-you").name, null, "a place, not a Lemma called needs-you");
    assert.equal(waitingFor("/t/outcomes").name, null);
});

test("a destination at an old address is read as the place it is sent on to", () => {
    assert.equal(waitingFor("/l/marketing/apps").name, "Marketing");
    assert.equal(waitingFor("/t/outcome/marketing/apps").name, "Marketing");
    assert.equal(waitingFor("/t/marketing/library").name, "Marketing", "a workspace address under /t");
    assert.equal(waitingFor("/o/outcome/marketing/apps").name, "Marketing");
    assert.equal(waitingFor("/o/marketing/library").name, "Marketing", "a workspace address, once it is under /t");
    assert.equal(waitingFor("https://app.example/o/marketing").name, "Marketing");
    assert.equal(waitingFor("/o/needs-you").name, null);
    assert.equal(waitingFor("/o").name, null);
});

test("the face is that teammate's own, wherever you arrived from", () => {
    // Derived from the pod id, so it is the same creature the rail draws for
    // them — arriving at a sign-in should not introduce a stranger.
    const fromLibrary = waitingFor("/t/marketing/library").faces[0];
    const fromRecord = waitingFor("https://app.example/t/marketing/record/invoices/7").faces[0];
    assert.equal(fromLibrary, fromRecord);
});

test("no destination is a crowd, not an empty page", () => {
    const waiting = waitingFor(null);
    assert.equal(waiting.name, null);
    assert.equal(waiting.faces.length, 3);
    assert.equal(new Set(waiting.faces).size, 3, "three different ones, not the same face three times");
});

test("the crowd is stable for a visit", () => {
    // Re-picked per render, they would shuffle while somebody typed.
    assert.deepEqual(waitingFor(null, "seed-a").faces, waitingFor(null, "seed-a").faces);
    assert.notDeepEqual(waitingFor(null, "seed-a").faces, waitingFor(null, "seed-b").faces);
});

test("a destination outside the workspace names nobody", () => {
    // A pod app or the front door is somewhere to go, not somebody to meet.
    assert.equal(waitingFor("/").name, null);
    assert.equal(waitingFor("/connect").faces.length, 3);
});

/** Naming a pod by its id, when the id is worth saying. */

test("a slug reads as a name", () => {
    assert.equal(sayableName("marketing"), "Marketing");
    assert.equal(sayableName("launch_craft"), "Launch Craft");
});

test("an identifier is not printed at somebody as though it were a name", () => {
    // "get back to a3f9c1e2-..." reads as a fault, not a teammate.
    assert.equal(sayableName("a3f9c1e2-4b5d-11ee-be56-0242ac120002"), null);
    assert.equal(sayableName("pod_9f3a1c2e4b5d6789"), null);
    assert.equal(sayableName("0123456789abcdef0123"), null);
    assert.equal(sayableName("12345"), null);
    assert.equal(sayableName(""), null);
});

test("an unnameable teammate still gets a face", () => {
    // The creature comes from the id and is right either way, so the page
    // shows who is waiting even when it cannot say their name.
    const waiting = waitingFor("/t/a3f9c1e2-4b5d-11ee-be56-0242ac120002/conversation");
    assert.equal(waiting.name, null);
    assert.equal(waiting.faces.length, 1);
});
