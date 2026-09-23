import test from "node:test";
import assert from "node:assert/strict";
import {
    accountName,
    accountTrouble,
    accountUsable,
    bestAccount,
    isCredentialConflict,
    readAccount,
    readConnector,
    type ConnectorAccount,
} from "../src/data/accounts.ts";

/** Both of these were read wrong against the real API, and both failures were
 *  silent — a page that looked like a placeholder, and a poll that waited for
 *  something that had already arrived. */

test("an account is CONNECTED, not ACTIVE", () => {
    // The whole detection bug. `ACTIVE` is not a status this API has, so the
    // check was never true and the authorisation never finished.
    assert.equal(accountUsable("CONNECTED", "READY"), true);
    assert.equal(accountUsable("ACTIVE", "READY"), false);
});

test("a live token that can reach nothing is not usable", () => {
    // Binding a surface to one makes a channel that exists and does not work.
    assert.equal(accountUsable("CONNECTED", "INSTALL_REQUIRED"), false);
    assert.equal(accountUsable("CONNECTED", "CHOOSE_INSTALL"), false);
    assert.equal(accountUsable("CONNECTED", "PENDING_APPROVAL"), false);
    // Absent is fine: only GitHub-style connectors report one.
    assert.equal(accountUsable("CONNECTED", ""), true);
});

test("a dead token is not usable either", () => {
    assert.equal(accountUsable("REAUTH_REQUIRED", "READY"), false);
    assert.equal(accountUsable("DISCONNECTED", "READY"), false);
});

test("a connector's display name is `title`, not `name`", () => {
    // The settings page read `name`, `slug` and `logo` — none of which exist.
    // Every row fell back to the raw id, which is what made it look unfinished.
    const connector = readConnector({ id: "slack", title: "Slack", icon: "/x.svg", is_active: true });
    assert.equal(connector?.title, "Slack");
    assert.equal(connector?.icon, "/x.svg");
});

test("a connector with no title falls back to its id rather than nothing", () => {
    assert.equal(readConnector({ id: "notion" })?.title, "notion");
    assert.equal(readConnector({}), null);
});

test("an account is named the way the provider named it", () => {
    assert.equal(readAccount({ id: "a", display_name: "Acme HQ", email: "x@y.z" })?.label, "Acme HQ");
    assert.equal(readAccount({ id: "a", email: "x@y.z" })?.label, "x@y.z");
});

test("a provider id is never a name", () => {
    // "Using U077S2UCG4S, already connected here" is what made a finished
    // screen read like a placeholder. The id is kept, just not as the name.
    const account = readAccount({ id: "a", provider_account_id: "U077S2UCG4S" }) as ConnectorAccount;
    assert.equal(account.label, "");
    assert.equal(account.ref, "U077S2UCG4S");
    assert.equal(accountName(account, "Slack"), "your Slack account");
});

test("a real name beats the connector's", () => {
    const account = readAccount({ id: "a", display_name: "Acme HQ" }) as ConnectorAccount;
    assert.equal(accountName(account, "Slack"), "Acme HQ");
});

test("with no name and no connector, it is still a sentence", () => {
    const account = readAccount({ id: "a" }) as ConnectorAccount;
    assert.equal(accountName(account, ""), "a connected account");
});

test("each unfinished state says the thing that can actually be done", () => {
    // "Reconnect" is advice that cannot succeed when you are waiting on an owner.
    const at = (status: string, install: string) =>
        accountTrouble(readAccount({ id: "a", status, install_state: install }) as ConnectorAccount);

    assert.equal(at("CONNECTED", "READY"), "");
    assert.equal(at("REAUTH_REQUIRED", "READY"), "Needs signing in again");
    assert.equal(at("CONNECTED", "INSTALL_REQUIRED"), "Needs installing before it can reach anything");
    assert.equal(at("CONNECTED", "PENDING_APPROVAL"), "Waiting on an organization owner");
});

test("the best account is a working one, and the default wins", () => {
    const accounts = [
        readAccount({ id: "old", connector_id: "slack", status: "CONNECTED", install_state: "READY", created_at: "2026-01-01" }),
        readAccount({ id: "broken", connector_id: "slack", status: "REAUTH_REQUIRED", created_at: "2026-09-01" }),
        readAccount({ id: "chosen", connector_id: "slack", status: "CONNECTED", install_state: "READY", is_default: true, created_at: "2026-02-01" }),
    ].filter((account): account is ConnectorAccount => account !== null);

    assert.equal(bestAccount(accounts, "slack")?.id, "chosen");
    assert.equal(bestAccount(accounts, "notion"), null);
});

test("with no default, the newest working account wins", () => {
    const accounts = [
        readAccount({ id: "old", connector_id: "slack", status: "CONNECTED", install_state: "READY", created_at: "2026-01-01" }),
        readAccount({ id: "new", connector_id: "slack", status: "CONNECTED", install_state: "READY", created_at: "2026-09-01" }),
    ].filter((account): account is ConnectorAccount => account !== null);

    assert.equal(bestAccount(accounts, "slack")?.id, "new");
});

test("an account that cannot reach anything is never chosen", () => {
    const accounts = [
        readAccount({ id: "half", connector_id: "github", status: "CONNECTED", install_state: "INSTALL_REQUIRED" }),
    ].filter((account): account is ConnectorAccount => account !== null);

    assert.equal(bestAccount(accounts, "github"), null);
});

test("a taken credential is a signal, not a failure to report", () => {
    // A connected account is claimable once per organization. The catalog
    // publishes only the system claim, so this 409 is the first and only
    // warning — and the answer to it is a bot of this teammate's own.
    assert.equal(
        isCredentialConflict(new Error("This connected account is already used by another surface in this organization.")),
        true,
    );
    assert.equal(isCredentialConflict(new Error("AGENT_SURFACE_CREDENTIAL_CONFLICT")), true);
    assert.equal(isCredentialConflict(new Error("Network request failed")), false);
    assert.equal(isCredentialConflict(null), false);
});
