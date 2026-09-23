import test from "node:test";
import assert from "node:assert/strict";
import { blurbOf, byEffort, readConnectable, type Connectable } from "../src/data/connectable.ts";

/** The catalog decides what a person is told BEFORE they click, which is the
 *  whole point of reading it — the old strip drew five identical grey icons and
 *  let you discover the difference by clicking. */

const read = (raw: unknown) => readConnectable(raw) as Connectable;

test("a shared identity this org can still take is one click", () => {
    const entry = read({
        platform: "TELEGRAM",
        supported_credential_modes: ["CUSTOM", "SYSTEM"],
        system_claim: { available: true },
        connector_available: true,
    });
    assert.equal(entry.effort, "instant");
    assert.equal(entry.systemFree, true);
});

test("a shared identity another pod already claimed is not", () => {
    // The claim is once per organization. Knowing that here is what turns a
    // failed save into a sentence on the row.
    const entry = read({
        platform: "WHATSAPP",
        supported_credential_modes: ["CUSTOM", "SYSTEM"],
        system_claim: { available: false, claimed_by_pod_id: "r2", claimed_by_surface_name: "whatsapp" },
        connector_available: true,
    });
    assert.equal(entry.effort, "account");
    assert.equal(entry.systemFree, false);
    assert.equal(entry.claimedBy?.podId, "r2");
});

test("the guided path is what is left when the shared one is gone", () => {
    const entry = read({
        platform: "TELEGRAM",
        supported_credential_modes: ["CUSTOM", "SYSTEM"],
        system_claim: { available: false },
        managed_setup_available: true,
        connector_available: true,
    });
    assert.equal(entry.effort, "guided");
});

test("a platform with no shared identity needs an account", () => {
    const entry = read({
        platform: "SLACK",
        supported_credential_modes: ["CUSTOM"],
        connector_available: true,
        connect: { system_oauth_available: true },
    });
    assert.equal(entry.effort, "account");
    assert.equal(entry.system, false);
    assert.equal(entry.hostedOAuth, true);
});

test("a platform with no route at all says so instead of offering one", () => {
    const entry = read({ platform: "TEAMS", supported_credential_modes: ["CUSTOM"], connector_available: false });
    assert.equal(entry.effort, "unavailable");
});

test("SYSTEM is never assumed from the platform name", () => {
    // It is configured per deployment. A Telegram that reports CUSTOM only
    // has no Lemma bot here, whatever it has elsewhere.
    const entry = read({ platform: "TELEGRAM", supported_credential_modes: ["CUSTOM"], connector_available: true });
    assert.equal(entry.system, false);
    assert.equal(entry.effort, "account");
});

test("silence about a claim is not the same as a claim being taken", () => {
    // No claim block means the backend had nothing to report, not that it is
    // spent — and hiding the one-click path is the costlier mistake.
    const entry = read({ platform: "RESEND", supported_credential_modes: ["CUSTOM", "SYSTEM"] });
    assert.equal(entry.systemFree, true);
    assert.equal(entry.effort, "instant");
});

test("an entry with no platform is not a row", () => {
    assert.equal(readConnectable({ supported_credential_modes: ["SYSTEM"] }), null);
    assert.equal(readConnectable(null), null);
});

test("cheapest first, and a dead end never above a live one", () => {
    const entries = [
        read({ platform: "TEAMS", supported_credential_modes: ["CUSTOM"], connector_available: false }),
        read({ platform: "SLACK", supported_credential_modes: ["CUSTOM"], connector_available: true }),
        read({ platform: "TELEGRAM", supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: { available: true } }),
        read({ platform: "WHATSAPP", supported_credential_modes: ["CUSTOM"], managed_setup_available: true }),
    ];
    assert.deepEqual(
        [...entries].sort(byEffort).map((entry) => entry.platform),
        ["TELEGRAM", "WHATSAPP", "SLACK", "TEAMS"],
    );
});

test("carries the connector an account is authorised against", () => {
    // Without it the row can start an OAuth for the wrong catalog entry, or
    // none at all.
    const entry = read({ platform: "SLACK", connector_id: "slack", supported_credential_modes: ["CUSTOM"] });
    assert.equal(entry.connectorId, "slack");
});

test("says what a channel is, not what the connector is", () => {
    // "Credential-managed Telegram bot surface connector for agent messaging"
    // is accurate documentation and the wrong thing to put in front of somebody
    // deciding where a colleague should answer.
    const entry = read({
        platform: "TELEGRAM",
        description: "Credential-managed Telegram bot surface connector for agent messaging.",
        supported_credential_modes: ["CUSTOM"],
    });
    assert.equal(blurbOf(entry), "A bot people message directly.");
});

test("an unknown platform keeps the catalog's own words", () => {
    // Developer copy beats a blank line for something this app has never met.
    const entry = read({ platform: "MATRIX", description: "Matrix homeserver bridge.", supported_credential_modes: ["CUSTOM"] });
    assert.equal(blurbOf(entry), "Matrix homeserver bridge.");
});
