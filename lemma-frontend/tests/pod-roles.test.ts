import assert from "node:assert/strict";
import { test } from "node:test";
import { readPodRoles } from "../src/data/pod-roles.ts";

test("a member reads as the strongest built-in role they hold", () => {
    assert.deepEqual(readPodRoles(["POD_VIEWER", "POD_ADMIN"]), { role: "Admin", can: "Can edit and add people" });
    assert.deepEqual(readPodRoles(["POD_EDITOR"]), { role: "Editor", can: "Can edit" });
    assert.deepEqual(readPodRoles(["POD_USER", "POD_VIEWER"]), { role: "User", can: "Can use" });
});

test("a custom role is named rather than dropped, and no role is a plain member", () => {
    assert.deepEqual(readPodRoles(["POD_RELEASE_MANAGER"]), { role: "Release manager", can: "—" });
    assert.deepEqual(readPodRoles([]), { role: "Member", can: "—" });
    assert.deepEqual(readPodRoles(undefined), { role: "Member", can: "—" });
});

test("the role a picker shows is the widest built-in one held", async () => {
    const { heldPodRole } = await import("../src/data/pod-roles.ts");
    assert.equal(heldPodRole(["POD_VIEWER", "POD_ADMIN"]), "POD_ADMIN");
    assert.equal(heldPodRole(["POD_RELEASE_MANAGER"]), "POD_RELEASE_MANAGER");
    // A custom role beside another: one picker value would drop it, so none.
    assert.equal(heldPodRole(["POD_USER", "POD_RELEASE_MANAGER"]), null);
    assert.equal(heldPodRole([]), null);
    assert.equal(heldPodRole(undefined), null);
});
