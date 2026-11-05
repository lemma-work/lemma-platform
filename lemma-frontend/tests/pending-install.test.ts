import test from "node:test";
import assert from "node:assert/strict";
import { arrivedFromInstall, forgetInstall, markInstallArrival, pendingInstall, PENDING_MS, rememberInstall } from "../src/site/import/pending-install.ts";

function memory() {
    const held = new Map<string, string>();
    return {
        getItem: (name: string) => held.get(name) ?? null,
        setItem: (name: string, value: string) => void held.set(name, value),
        removeItem: (name: string) => void held.delete(name),
    };
}

const ledger = { path: "/import/github/ada-member/ledger-pod", title: "Ledger Pod" };

test("an install that was started is offered back until it finishes", () => {
    const store = memory();
    rememberInstall(store, ledger, 1_000);
    assert.deepEqual(pendingInstall(store, 2_000), ledger);
    forgetInstall(store);
    assert.equal(pendingInstall(store, 2_000), null);
});

test("an install from yesterday is not offered", () => {
    const store = memory();
    rememberInstall(store, ledger, 1_000);
    assert.equal(pendingInstall(store, 1_000 + PENDING_MS + 1), null);
});

test("only an install page is ever offered", () => {
    const store = memory();
    for (const path of ["https://evil.example/import/github/a/b", "//evil.example/x", "/t/pod", "javascript:alert(1)"]) {
        rememberInstall(store, { path, title: "x" }, 1_000);
        assert.equal(pendingInstall(store, 2_000), null, path);
    }
    store.setItem("lemma-app:install-pending", JSON.stringify({ path: "//evil.example/", title: "x", at: 1_000 }));
    assert.equal(pendingInstall(store, 2_000), null);
});

test("the visit an install opens is marked, and only that store", () => {
    const tab = memory();
    assert.equal(arrivedFromInstall(tab), false);
    markInstallArrival(tab);
    assert.equal(arrivedFromInstall(tab), true);
    assert.equal(arrivedFromInstall(memory()), false);
});
