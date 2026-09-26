import test from "node:test";
import assert from "node:assert/strict";
import { makeTeammate } from "../src/stage/hiring-steps.ts";

function counting() {
    let made = 0;
    return {
        get made() { return made; },
        create: async () => { made += 1; return { id: "pod-" + made }; },
    };
}

test("a face that fails to save does not fail the hire", async () => {
    const pods = counting();
    const result = await makeTeammate({
        existing: null,
        create: pods.create,
        variantFor: () => 3,
        saveFace: async () => { throw new Error("icon refused"); },
    });
    assert.equal(pods.made, 1);
    assert.deepEqual(result, { pod: { id: "pod-1" }, variant: 0, faceSaved: false });
});

test("a retry reuses the pod the first attempt created instead of making another", async () => {
    const pods = counting();
    let kept: { id: string } | null = null;
    await assert.rejects(makeTeammate({
        existing: kept,
        create: pods.create,
        onCreated: (pod) => { kept = pod; },
        variantFor: () => { throw new Error("after the create"); },
        saveFace: async () => undefined,
    }));
    assert.equal(pods.made, 1);
    const second = await makeTeammate({
        existing: kept,
        create: pods.create,
        variantFor: () => 2,
        saveFace: async () => undefined,
    });
    assert.equal(pods.made, 1);
    assert.deepEqual(second, { pod: { id: "pod-1" }, variant: 2, faceSaved: true });
});

test("the default face is not written at all", async () => {
    const pods = counting();
    let writes = 0;
    const result = await makeTeammate({
        existing: null,
        create: pods.create,
        variantFor: () => 0,
        saveFace: async () => { writes += 1; },
    });
    assert.equal(writes, 0);
    assert.equal(result.faceSaved, true);
});
