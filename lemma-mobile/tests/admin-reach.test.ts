import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
    addressOf, answerChoices, answererChoices, answererName, arrivedGroup, connectPlan, connectProblem, connectWay, costOf, groupLine,
    guidedState, healthOf, heldElsewhere, linkProblem, listHint, noGroupsYet, offerOf, ownPlaces, placeKey, placeName, placeNeedsYou, placeStatus,
    quickWay, reachCount, reachLine, sayHi, sayHiLabel, slackManifestLink, startGroupProblem, systemLine, why, workspaceName,
} from "../src/areas/admin/reach/places";
import { reachSample } from "../src/areas/admin/reach/sample";
import type { Surface } from "../src/areas/admin/reach/types";
import { readConnectable, type Connectable } from "@web/data/connectable";
import { readPodConnector, type PodConnector } from "@web/data/pod-connectors";

/** Where a Lemma is reached (PARITY.md, F): what each place costs, what the
 *  list and a place's screen say, and the sample world they are tried on. */

function entry(raw: Record<string, unknown>): Connectable {
    const read = readConnectable(raw);
    if (!read) throw new Error("unreadable");
    return read;
}

function surface(patch: Partial<Surface>): Surface {
    return { id: "s", platform: "WHATSAPP", name: "whatsapp", mine: true, agentName: "Support", handle: "+1 555-0142", active: true, status: "ACTIVE", ...patch };
}

const TOKEN = { type: "object", required: ["bot_token"], properties: { bot_token: { type: "string" } } };

const TELEGRAM = entry({ platform: "TELEGRAM", connector_id: "telegram", title: "Telegram", connector_available: true, managed_setup_available: true, supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: { available: true }, connect: { credential_schema: TOKEN } });
const WHATSAPP_TAKEN = entry({ platform: "WHATSAPP", connector_id: "whatsapp", title: "WhatsApp", connector_available: true, supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: { available: false, claimed_by_pod_id: "other" } });
const EMAIL = entry({ platform: "RESEND", connector_id: "resend", title: "Email", email_domain: "inbox.example", supported_credential_modes: ["SYSTEM"], system_claim: { available: true } });
const SLACK = entry({ platform: "SLACK", connector_id: "slack", title: "Slack", connector_available: true, supported_credential_modes: ["CUSTOM"], connect: { system_oauth_available: true } });
const TEAMS = entry({ platform: "TEAMS", connector_id: "teams", title: "Microsoft Teams", connector_available: true, supported_credential_modes: ["CUSTOM"] });
const DISCORD = entry({ platform: "DISCORD", connector_id: "discord", title: "Discord", supported_credential_modes: ["CUSTOM"] });
const NO_LINK = entry({ platform: "TEAMS", connector_id: "teams", title: "Microsoft Teams", connector_available: true, supported_credential_modes: ["CUSTOM"], unavailable_reason: "NEEDS_PUBLIC_LINK" });

function connector(raw: Record<string, unknown>): PodConnector {
    const read = readPodConnector({ mode: "SHARED", status: "READY", ...raw });
    if (!read) throw new Error("unreadable");
    return read;
}

describe("names and addresses", () => {
    it("says one name per place, email's four platforms as one", () => {
        expect(placeKey("resend")).toBe("EMAIL");
        expect(placeKey("GMAIL")).toBe("EMAIL");
        expect(placeName("RESEND")).toBe("Email");
        expect(placeName("TEAMS")).toBe("Microsoft Teams");
        expect(placeName("DISCORD")).toBe("Discord");
        expect(placeName("MATRIX")).toBe("Matrix");
    });

    it("links to a conversation where the platform has one", () => {
        expect(sayHi(surface({ handle: "+1 555-0142" }))).toBe("https://wa.me/15550142");
        expect(sayHi(surface({ platform: "TELEGRAM", handle: "@acme_launch_bot" }))).toBe("https://t.me/acme_launch_bot");
        expect(sayHi(surface({ platform: "RESEND", handle: "x", email: "mailto:support\\@inbox.example" }))).toBe("mailto:support@inbox.example");
        expect(sayHi(surface({ platform: "SLACK", handle: "Support" }))).toBeNull();
        expect(sayHi(surface({ handle: "" }))).toBeNull();
        expect(addressOf(surface({ platform: "RESEND", handle: "support@inbox.example" }))).toBe("support@inbox.example");
        expect(sayHiLabel("RESEND")).toBe("Write to it");
        expect(sayHiLabel("WHATSAPP")).toBe("Say hi in WhatsApp");
    });

    it("says whether a place works, and when it waits on somebody", () => {
        expect(placeStatus("PENDING_ADMIN_CONSENT", false)).toBe("Needs administrator approval");
        expect(placeStatus("INACTIVE", false)).toBe("Paused");
        expect(placeStatus(undefined, true)).toBe("Connected");
        expect(placeNeedsYou({ status: "NEEDS_SETUP", active: false })).toBe(true);
        expect(placeNeedsYou({ status: "INACTIVE", active: false })).toBe(false);
        expect(placeNeedsYou({ status: "ACTIVE", active: true })).toBe(false);
    });

    it("says the Settings line: where it is reached now, else only in Lemma", () => {
        expect(reachLine([])).toBe("Only here, in Lemma");
        expect(reachLine([surface({}), surface({ id: "e", platform: "RESEND" }), surface({ id: "p", platform: "SLACK", active: false })])).toBe("WhatsApp, Email");
        expect(reachCount("Support", 0)).toBe("Support can only be reached here, in Lemma.");
        expect(ownPlaces([surface({}), surface({ id: "a", mine: false })])).toHaveLength(1);
    });
});

describe("what connecting costs", () => {
    it("leads with Lemma's own identity when the organization can still have it", () => {
        const plan = connectPlan(TELEGRAM, [], false);
        expect(plan.action).toBe("connect");
        expect(quickWay(plan)).toBe(true);
        expect(plan.ownWay).toBe(true);
        expect(systemLine(TELEGRAM, "Hiring")).toBe("Lemma's own Telegram bot answers as Hiring.");
        expect(systemLine(EMAIL, "Hiring")).toContain("inbox.example");
    });

    it("offers a shared connector the Lemma has, unless another Lemma likely holds its account", () => {
        const teams = connector({ slug: "teams", connector_id: "teams", shared_account: { display_name: "Acme on Microsoft 365" } });
        const free = connectPlan(TEAMS, [teams], false);
        expect(free.action).toBe("use-account");
        expect(free.podConnector).toBe("teams");
        expect(free.cost).toBe("Ready now");
        expect(free.using).toBe("Using teams (Acme on Microsoft 365), already shared in this Lemma.");
        expect(listHint(free)).toBe("Ready now, using teams (Acme on Microsoft 365)");
        /* Each person's own account cannot answer for everyone, and one with
           no account shared yet cannot answer at all. */
        expect(connectPlan(TEAMS, [{ ...teams, mode: "PER_MEMBER" }], false).action).toBe("set-up");
        expect(connectPlan(TEAMS, [{ ...teams, status: "NEEDS_ACCOUNT" }], false).action).toBe("set-up");
        const taken = connectPlan(TEAMS, [teams], true);
        expect(listHint(taken)).toBe("Already in use elsewhere here. It needs one of its own.");
        expect(taken.action).toBe("own");
        expect(taken.cost).toBe("Already in use");
        expect(taken.inUse).toContain("needs an identity of its own");
        expect(taken.ownWay).toBe(false);
    });

    it("never hands a Slack account to another Lemma", () => {
        const plan = connectPlan(SLACK, [connector({ slug: "slack", connector_id: "slack" })], false);
        expect(plan.action).toBe("set-up");
        expect(plan.using).toBeNull();
        expect(connectWay(SLACK, { custom: false, ownApp: false })).toBe("slack-manager");
        expect(connectWay(SLACK, { custom: false, ownApp: true })).toBe("own-app");
    });

    it("says why the shared identity is out, and what a bot token costs", () => {
        expect(why(WHATSAPP_TAKEN)).toBe("Lemma's shared number is already taken elsewhere in this organization.");
        expect(why(EMAIL)).toBe("An address of its own on inbox.example.");
        const ownToken = entry({ platform: "TELEGRAM", connector_id: "telegram", connector_available: true, supported_credential_modes: ["CUSTOM"], connect: { credential_schema: TOKEN } });
        expect(costOf(ownToken)).toBe("Needs a bot token");
        expect(connectWay(ownToken, { custom: false, ownApp: false })).toBe("credentials");
    });

    it("chooses Telegram's way: the setup bot, or a bot already made", () => {
        expect(connectWay(TELEGRAM, { custom: false, ownApp: false })).toBe("guided");
        expect(connectWay(TELEGRAM, { custom: true, ownApp: false })).toBe("credentials");
    });

    it("blocks what the server would refuse, and opens what it has no way in for", () => {
        expect(connectPlan(NO_LINK, [], false).action).toBe("blocked");
        expect(connectPlan(NO_LINK, [], false).note).toBe("Needs a public link to this server first.");
        const discord = connectPlan(DISCORD, [], false);
        expect(discord.action).toBe("set-up");
        expect(listHint(discord)).toBe("Not available · In the servers your community already uses.");
        expect(listHint(connectPlan(TELEGRAM, [], false))).toBe("Ready now");
        expect(discord.cost).toBe("Not available");
        expect(connectWay(DISCORD, { custom: false, ownApp: false })).toBe("none");
    });

    it("offers only what does not answer already, once the places are read", () => {
        expect(offerOf([TELEGRAM, EMAIL], [surface({ platform: "RESEND" })], false)).toEqual([]);
        expect(offerOf([TELEGRAM, EMAIL], [surface({ platform: "RESEND" })], true).map((one) => one.platform)).toEqual(["TELEGRAM"]);
        const held = heldElsewhere([{ platform: "SLACK", podId: "other", name: "slack" }, { platform: "TEAMS", podId: "here", name: "teams" }], "here", [surface({ platform: "TELEGRAM", mine: false })]);
        expect([...held].sort()).toEqual(["SLACK", "TELEGRAM"]);
    });

    it("says refusals as a person would", () => {
        expect(connectProblem(new Error("AGENT_SURFACE_CREDENTIAL_CONFLICT"), "x")).toBe("That account already answers as another Lemma in this organization.");
        expect(connectProblem(Object.assign(new Error("nope"), { statusCode: 403 }), "x")).toBe("You may not change where this Lemma is reached.");
        expect(connectProblem(new Error(""), "Fallback.")).toBe("Fallback.");
    });
});

describe("a place's screen", () => {
    it("tells a Telegram setup's state", () => {
        const now = Date.parse("2026-10-24T10:00:00Z");
        const base = { setupId: "s", launchUrl: "", managerBot: "", expiresAt: "2026-10-24T10:15:00Z" };
        expect(guidedState(null)).toBe("none");
        expect(guidedState({ ...base, status: "PENDING" }, now)).toBe("waiting");
        expect(guidedState({ ...base, status: "READY" }, now)).toBe("ready");
        expect(guidedState({ ...base, status: "PENDING", expiresAt: "2026-10-24T09:00:00Z" }, now)).toBe("failed");
    });

    it("says whether it works in one line", () => {
        expect(healthOf(undefined, true, false).text).toBe("Checking it works…");
        expect(healthOf({ ready: false }, false, false)).toEqual({ text: "Not finished yet. The steps left are below.", tone: "waiting" });
        expect(healthOf({ ready: true, status: "INACTIVE" }, false, false).text).toBe("Connected, and paused");
        expect(healthOf({ ready: true, status: "ACTIVE" }, false, false).tone).toBe("good");
    });

    it("keeps an agent the list no longer has, so opening never changes it", () => {
        const agents = [{ name: "pod_default", label: "Support", front: true }, { name: "refunds", label: "Refunds", front: false }];
        expect(answererChoices("Support", agents, "pod_default").map((one) => one.value)).toEqual(["pod_default", "refunds"]);
        expect(answererChoices("Support", agents, "gone").map((one) => one.value)).toEqual(["pod_default", "gone", "refunds"]);
        expect(answererName("refunds", "Support", agents, "Support")).toBe("Refunds");
        expect(answererName("pod_default", "Support", agents, "x")).toBe("Support");
        expect(answerChoices("Support").map((one) => one.value)).toEqual(["off", "known", "anyone"]);
    });

    it("says a group's people outside, and how a group gets there", () => {
        expect(groupLine({ kind: "someone", name: "Sam Okafor" }, "Support")).toBe("Sam Okafor answers for people outside Support");
        expect(groupLine({ kind: "bot-off", platform: "WHATSAPP" }, "Support")).toBe("Answering people outside Support is off here");
        expect(noGroupsYet("WHATSAPP", "Support")).toBe("Start a WhatsApp group on Support's number and it shows up here.");
        expect(arrivedGroup(null, [{ id: "a" }])).toBeNull();
        expect(arrivedGroup(new Set(["a"]), [{ id: "a" }, { id: "b" }])).toEqual({ id: "b" });
        expect(startGroupProblem(Object.assign(new Error("x"), { statusCode: 502 }), "Support")).toBe("WhatsApp would not start the group. Try again shortly.");
        expect(linkProblem(Object.assign(new Error("Telegram is busy"), { statusCode: 409 }), "Support")).toBe("Telegram is busy.");
    });

    it("opens Slack's new-app page with the manifest in it", () => {
        expect(slackManifestLink({ a: 1 })).toBe("https://api.slack.com/apps?new_app=1&manifest_json=%7B%22a%22%3A1%7D");
        expect(workspaceName({ teamId: "T1", teamName: null })).toBe("T1");
    });
});

describe("the sample world", () => {
    afterEach(() => { vi.useRealTimers(); });

    async function settle<T>(promise: Promise<T>): Promise<T> {
        await vi.runAllTimersAsync();
        return promise;
    }

    it("offers Hiring each way in", async () => {
        vi.useFakeTimers();
        const sample = reachSample();
        const catalog = await settle(sample.catalog("sample-hiring-1078"));
        const connectors = await settle(sample.podConnectors("sample-hiring-1078"));
        const mine = await settle(sample.mine());
        const here = await settle(sample.places("sample-hiring-1078"));
        const plans = Object.fromEntries(catalog.map((one) => [one.platform, connectPlan(one, connectors, heldElsewhere(mine, "sample-hiring-1078", here).has(placeKey(one.platform))).action]));
        expect(here).toEqual([]);
        /* Slack and WhatsApp: the organization's accounts there are likely
           Support's, so Hiring is offered an identity of its own. */
        expect(plans).toEqual({ TELEGRAM: "connect", RESEND: "connect", WHATSAPP: "own", TEAMS: "use-account", SLACK: "own", DISCORD: "set-up" });
    });

    it("refuses an account another Lemma answers on, as the server does", async () => {
        vi.useFakeTimers();
        const sample = reachSample();
        const refused = expect(sample.connectOwnAccount("sample-hiring-1078", "SLACK", "acc-slack-support")).rejects.toThrow(/already used by another surface/);
        await vi.runAllTimersAsync();
        await refused;
    });

    it("keeps a change to a place", async () => {
        vi.useFakeTimers();
        const sample = reachSample();
        await settle(sample.update("sample-support-1140", "whatsapp", { is_enabled: false, config: { send_policy: { allow_send: true } } }));
        const [after] = (await settle(sample.places("sample-support-1140"))).filter((one) => one.name === "whatsapp");
        expect(after.status).toBe("INACTIVE");
        const detail = await settle(sample.detail("sample-support-1140", "whatsapp"));
        expect(detail.config.send_policy?.allow_send).toBe(true);
        await settle(sample.update("sample-support-1140", "whatsapp", { is_enabled: true }));
    });
});

describe("the copy", () => {
    const root = fileURLToPath(new URL("../src/areas/admin/", import.meta.url));
    const files = ["reach", "account"].flatMap((part) => readdirSync(root + part).filter((name) => /\.tsx?$/.test(name)).map((name) => `${part}/${name}`));

    /** The words a person reads: every quoted string and template, less what
     *  is code inside a template. */
    function said(source: string): string[] {
        /* Comments, and where an import comes from, are code, not copy. */
        const text = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/^(?:import|export)\b[^;]*?from "[^"]*";$/gm, "");
        const found: string[] = [];
        for (const match of text.matchAll(/"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g)) {
            const words = (match[1] ?? match[2] ?? "").replace(/\$\{[^}]*\}/g, " ");
            /* A key, an id or a path is code, not copy: `lemmaPart("channels")`. */
            if (!/^[a-z][a-z0-9-]*$/.test(words)) found.push(words);
        }
        for (const match of text.matchAll(/>([^<>{}\n]*[A-Za-z][^<>{}\n]*)</g)) found.push(match[1]);
        return found;
    }

    it("never says channel, pod, teammate, goal, mission or line", () => {
        for (const file of files) {
            for (const words of said(readFileSync(root + file, "utf8"))) {
                expect(words, `${file}: ${words}`).not.toMatch(/\bchannels?\b|\bpods?\b|teammate|\bgoals?\b|\bmissions?\b|\blines?\b/i);
            }
        }
    });
});
