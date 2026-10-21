import test from "node:test";
import assert from "node:assert/strict";
import { createOrReplace } from "../src/library/replace-on-conflict.ts";

class Refused extends Error {
    readonly statusCode: number;
    constructor(statusCode: number) {
        super(`refused ${statusCode}`);
        this.statusCode = statusCode;
    }
}

test("a new name is created, and nothing is replaced", async () => {
    let replaced = false;
    const made = await createOrReplace(async () => "created", async () => { replaced = true; return "replaced"; });
    assert.equal(made, "created");
    assert.equal(replaced, false);
});

test("a name already taken replaces that file instead of failing", async () => {
    const made = await createOrReplace(async () => { throw new Refused(409); }, async () => "replaced");
    assert.equal(made, "replaced");
});

test("any other refusal is the caller's, and nothing is replaced", async () => {
    let replaced = false;
    await assert.rejects(
        createOrReplace(async () => { throw new Refused(413); }, async () => { replaced = true; return "replaced"; }),
        /refused 413/,
    );
    assert.equal(replaced, false);
});
