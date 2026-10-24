import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { sampleChatOf } from "../src/areas/chat/sample-chats";
import {
    countLine, displaySize, entryNote, folderRows, liveWords, opensInBrowser, outcomeWords, readLimit, reasonLine, retries,
    saving, screenSocketUrl, signInTrouble, signOutAsk, siteGroups, SAMPLE_SCREEN, SIGN_IN_FINE, SIGN_IN_NOT_YOURS, sizeLine,
    startingFolder, startupLine, stateLine, statusEvery, tooLargeToShow, TOO_LARGE_TO_SAVE, WHOSE, whereSay,
} from "../src/areas/more/computer/model";
import { childrenOf, SAMPLE_HOME, SAMPLE_SIGN_IN, SAMPLE_WORKSPACE, sampleChatFolders, sampleLogins, sampleTree, utf8Length } from "../src/areas/more/computer/sample-tree";
import * as screenPage from "../src/areas/more/computer/vnc-page.generated";
import { MAX_READ_BYTES, MAX_TEXT_BYTES, type Entry, type Listing } from "@web/computer/machine";
import { socketUrl, type LiveState } from "@web/computer/live";
import { base64Of, bytesOf, fromPage, KEY, KEYBOARD_PAD, keysymOf, needsRefill, specialKeysym, toPageScript, typedKeys } from "../src/areas/more/computer/wire";

/** The computer (PARITY.md, G): the web's modules copied under
 *  `computer/web/`, the screen's page built from its sources, the bytes and
 *  keys that pass between the app and that page, and what every state says. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
function stamp(inputs: string[]): string {
    const hash = createHash("sha256");
    for (const file of [...inputs].sort()) {
        hash.update(file + "\0");
        hash.update(readFileSync(mobile + file));
    }
    return hash.digest("hex").slice(0, 16);
}

describe("the screen's page", () => {
    it.runIf(screenPage.INPUTS.every((file) => existsSync(mobile + file)))("is built from its sources as they are (npm run build -- vnc in webview/ if not)", () => {
        expect(screenPage.INPUTS).toContain("webview/src/vnc/main.ts");
        expect(screenPage.INPUTS).toContain("src/areas/more/computer/wire.ts");
        expect(stamp(screenPage.INPUTS)).toBe(screenPage.STAMP);
    });

    it("carries noVNC and the bridge, and nothing it could reach the API by", () => {
        expect(screenPage.HTML).toContain("Raw channel missing property");
        expect(screenPage.HTML).toContain("__vnc");
        expect(screenPage.HTML).not.toMatch(/api\.lemma\.work|localhost:8761|wss?:\/\//);
        expect(screenPage.HTML).not.toMatch(/Authorization|Bearer |access_token/);
    });
});

describe("bytes as base64, both ways", () => {
    const bytes = (length: number) => Uint8Array.from({ length }, (_, at) => (at * 37 + 11) % 256);

    it("encodes as the standard does, in pieces of any length", () => {
        for (const length of [0, 1, 2, 3, 4, 5, 12_287, 12_288, 12_289, 100_000]) {
            const made = bytes(length);
            expect(base64Of(made)).toBe(Buffer.from(made).toString("base64"));
        }
    });

    it("decodes what it encoded, byte for byte", () => {
        for (const length of [0, 1, 2, 3, 16_383, 16_384, 70_001]) {
            const made = bytes(length);
            const back = bytesOf(base64Of(made));
            expect(back.byteLength).toBe(length);
            expect(Buffer.from(back).equals(Buffer.from(made))).toBe(true);
        }
    });

    it("encodes a view of a larger buffer as only its own bytes", () => {
        const whole = bytes(64);
        expect(base64Of(whole.subarray(10, 20))).toBe(Buffer.from(whole.subarray(10, 20)).toString("base64"));
    });
});

describe("typing on a phone, as key presses", () => {
    it("gives Latin-1 its own keysym and everything else the Unicode one", () => {
        expect(keysymOf("a".codePointAt(0)!)).toBe(0x61);
        expect(keysymOf("Z".codePointAt(0)!)).toBe(0x5a);
        expect(keysymOf(" ".codePointAt(0)!)).toBe(0x20);
        expect(keysymOf("é".codePointAt(0)!)).toBe(0xe9);
        expect(keysymOf("€".codePointAt(0)!)).toBe(0x010020ac);
        expect(keysymOf("😀".codePointAt(0)!)).toBe(0x0101f600);
        expect(keysymOf(0x0a)).toBe(KEY.enter);
        expect(keysymOf(0x09)).toBe(KEY.tab);
        expect(keysymOf(0x07)).toBeNull();
        expect(keysymOf(0x85)).toBeNull();
        expect(KEY.backspace).toBe(0xff08);
        expect(KEY.enter).toBe(0xff0d);
        expect(KEY.tab).toBe(0xff09);
    });

    it("reads what changed in the box as noVNC's own page does", () => {
        expect(typedKeys("abc", "abcd")).toEqual([0x64]);
        expect(typedKeys("abc", "ab")).toEqual([KEY.backspace]);
        /* An autocorrection: rub out from the first difference, type again. */
        expect(typedKeys("__teh", "__the")).toEqual([KEY.backspace, KEY.backspace, 0x68, 0x65]);
        expect(typedKeys("__", "__\n")).toEqual([KEY.enter]);
        expect(typedKeys("__", "__")).toEqual([]);
        /* A character outside the BMP is one key, and one Backspace. */
        expect(typedKeys("__", "__😀")).toEqual([0x0101f600]);
        expect(typedKeys("__😀", "__")).toEqual([KEY.backspace]);
    });

    it("keeps the box filled so Backspace always has something to take", () => {
        expect(needsRefill(KEYBOARD_PAD)).toBe(false);
        expect(needsRefill("")).toBe(true);
        expect(needsRefill(KEYBOARD_PAD.repeat(3))).toBe(true);
    });

    it("presses the keys a hardware keyboard types no character for", () => {
        expect(specialKeysym("ArrowLeft")).toBe(0xff51);
        expect(specialKeysym("Escape")).toBe(0xff1b);
        expect(specialKeysym("Tab")).toBe(KEY.tab);
        expect(specialKeysym("a")).toBeNull();
        expect(specialKeysym("Enter")).toBeNull();
    });
});

describe("the messages between the app and the page", () => {
    it("reads only what the page says", () => {
        expect(fromPage(JSON.stringify({ type: "ready" }))).toEqual({ type: "ready" });
        expect(fromPage(JSON.stringify({ type: "send", data: "AAE=" }))).toEqual({ type: "send", data: "AAE=" });
        expect(fromPage(JSON.stringify({ type: "send", data: 3 }))).toBeNull();
        expect(fromPage(JSON.stringify({ type: "disconnected", clean: true }))).toEqual({ type: "disconnected", clean: true });
        expect(fromPage(JSON.stringify({ type: "clipboard", text: "copied" }))).toEqual({ type: "clipboard", text: "copied" });
        expect(fromPage(JSON.stringify({ type: "paste-request" }))).toEqual({ type: "paste-request" });
        expect(fromPage(JSON.stringify({ type: "done" }))).toEqual({ type: "done" });
        expect(fromPage(JSON.stringify({ type: "navigate", url: "https://example.com" }))).toBeNull();
        expect(fromPage("not json")).toBeNull();
    });

    it("hands the page one message as a script", () => {
        expect(toPageScript({ type: "keys", keys: [0x61] })).toBe('window.__vnc&&window.__vnc({"type":"keys","keys":[97]});true;');
        expect(toPageScript({ type: "paste", text: "</script>\"" })).toContain(JSON.stringify("</script>\""));
    });
});

describe("the live screen's socket", () => {
    it("names the site for a sign-in, else the chat, and never a credential", () => {
        expect(screenSocketUrl("https://api.example.com", { origin: "https://app.prtracker.example" }))
            .toBe("wss://api.example.com/workspace/browser/view?mode=control&origin=https%3A%2F%2Fapp.prtracker.example");
        expect(screenSocketUrl("http://10.0.2.2:8000/", { conversationId: "c-1" })).toBe("ws://10.0.2.2:8000/workspace/browser/view?mode=control&conversation=c-1");
        expect(screenSocketUrl("https://api.example.com", { origin: "https://a.example", conversationId: "c-1" })).not.toContain("conversation=");
        expect(screenSocketUrl("https://api.example.com", {})).toBe("wss://api.example.com/workspace/browser/view?mode=control");
        expect(screenSocketUrl("https://api.example.com", { conversationId: "c-1" })).not.toMatch(/token/i);
    });

    it("is the web's address without its token", () => {
        for (const target of [{ origin: "https://app.prtracker.example" }, { conversationId: "8d2f-1" }, {}]) {
            expect(screenSocketUrl("https://api.example.com", target)).toBe(socketUrl("https://api.example.com", { mode: "control", ...target }));
        }
    });

    it("asks the display for a size the server accepts", () => {
        expect(displaySize({ width: 358.4, height: 236.2 })).toEqual({ width: 358, height: 240 });
        expect(displaySize({ width: 200, height: 9000 })).toEqual({ width: 320, height: 4096 });
        expect(displaySize({ width: Number.NaN, height: 700 })).toEqual({ width: 320, height: 700 });
    });
});

const listing = (extra: Partial<Listing> = {}): Listing => ({ path: SAMPLE_WORKSPACE, home_root: SAMPLE_HOME, workspace_root: SAMPLE_WORKSPACE, entries: [], ...extra });

describe("what the computer's state says", () => {
    it("says checking, then what the answers mean, with the right dot", () => {
        expect(stateLine(undefined, undefined, true)).toMatchObject({ state: "checking", headline: "Checking", action: null, dot: "off" });
        expect(stateLine(undefined, undefined, false)).toMatchObject({ state: "unreachable", headline: "Unknown", action: null, dot: "amber" });
        expect(stateLine(listing({ sleeping: true }), undefined, false)).toMatchObject({ state: "asleep", headline: "Asleep", action: "wake", dot: "off" });
        expect(stateLine(listing(), "running", false)).toMatchObject({ state: "browsing", headline: "In use", action: "show", dot: "violet" });
        expect(stateLine(listing(), "stopped", false)).toMatchObject({ state: "awake", note: "Nothing on screen right now.", action: "show", dot: "on" });
        expect(stateLine(listing(), "unsupported", false)).toMatchObject({ state: "awake", note: "This computer has no screen to show.", action: null });
    });

    it("shows startup progress while it downloads or starts, and polls faster then", () => {
        expect(startupLine({ state: "downloading", detail: "Fetching", done_mb: 300, total_mb: 1200 })).toEqual({ title: "Setting up your computer", detail: "Fetching", fraction: 0.25, downloaded: "300 of 1200 MB" });
        expect(startupLine({ state: "starting" })).toMatchObject({ title: "Starting your computer", fraction: null, downloaded: null });
        expect(startupLine({ state: "ready" })).toBeNull();
        expect(statusEvery({ state: "starting" })).toBe(3_000);
        expect(statusEvery({ state: "ready" })).toBe(15_000);
        expect(statusEvery(undefined)).toBe(15_000);
    });

    const states: LiveState[] = ["connecting", "live", "lost", "refused", "signed-out", "no-browser", "unsupported", "stale-image"];

    it("says every live state, and offers the phone's browser only where that can help", () => {
        for (const state of states) expect(liveWords(state)).toBeTruthy();
        expect(states.filter(opensInBrowser)).toEqual(["refused", "unsupported", "stale-image"]);
        expect(states.filter(retries)).toEqual(["lost", "no-browser"]);
        for (const state of states.filter((one) => one !== "live")) expect(signInTrouble(state)).toBeTruthy();
    });
});

describe("files", () => {
    const roots = { home: SAMPLE_HOME, workspace: SAMPLE_WORKSPACE };

    it("says what a folder is for, the phone's way", () => {
        expect(whereSay(SAMPLE_HOME, roots)).toBe("The whole computer. It’s yours: all your work happens on it.");
        expect(whereSay(SAMPLE_WORKSPACE, roots)).toBe("Where chats and checkouts go.");
        expect(whereSay(SAMPLE_WORKSPACE + "/c", roots)).toBe("One folder per chat.");
        expect(whereSay(SAMPLE_WORKSPACE + "/c/2026-10-24", roots)).toBe("Chats started on 2026-10-24.");
        expect(whereSay(SAMPLE_WORKSPACE + "/c/2026-10-24/press", roots)).toBe("Where one chat’s commands run.");
        expect(whereSay(SAMPLE_WORKSPACE + "/repos", roots)).toBe("Projects checked out on this computer.");
        expect(whereSay(SAMPLE_WORKSPACE + "/repos/acme", roots)).toBe("Projects belonging to acme.");
        expect(whereSay(SAMPLE_WORKSPACE + "/repos/acme/site", roots)).toBe("acme/site: one checkout, shared by every chat working on it.");
        expect(whereSay(SAMPLE_WORKSPACE + "/repos/acme/site/src", roots)).toBeNull();
    });

    it("opens on the chat's folder until somebody walks elsewhere", () => {
        expect(startingFolder(null, "/home/user/lemma/c/x")).toBe("/home/user/lemma/c/x");
        expect(startingFolder("/home/user", "/home/user/lemma/c/x")).toBe("/home/user");
        expect(startingFolder(null, undefined)).toBeNull();
    });

    const entry = (name: string, kind: Entry["kind"] = "file", size = 10): Entry => ({ path: "/home/user/" + name, name, kind, size_bytes: size, modified_at: "2026-10-24T09:00:00Z" });

    it("puts folders first and holds back build output and dotfiles", () => {
        const all = [entry("b.txt"), entry("node_modules", "directory"), entry("src", "directory"), entry(".env"), entry("a.md")];
        expect(folderRows(all, false).shown.map((one) => one.name)).toEqual(["src", "a.md", "b.txt"]);
        expect(folderRows(all, false).hidden).toBe(2);
        expect(folderRows(all, true).shown.map((one) => one.name)).toEqual(["node_modules", "src", ".env", "a.md", "b.txt"]);
        expect(countLine(3, 2)).toBe("3 items · 2 hidden");
        expect(countLine(1, 0)).toBe("1 item");
        expect(entryNote(entry("a.md", "file", 2048))).toBe("2 KB");
        expect(entryNote(entry("empty.txt", "file", 0))).toBe("Empty");
        expect(entryNote(entry("link", "symlink", 12))).toBe("12 B, a link");
    });

    it("reads a file only as far as a phone shows it, and saves up to the server's one read", () => {
        expect(readLimit("text")).toBe(MAX_TEXT_BYTES + 1);
        expect(readLimit("image")).toBe(MAX_READ_BYTES);
        expect(tooLargeToShow("markdown", MAX_TEXT_BYTES)).toBe(false);
        expect(tooLargeToShow("text", MAX_TEXT_BYTES + 1)).toBe(true);
        expect(tooLargeToShow("image", MAX_READ_BYTES)).toBe(true);
        expect(saving(MAX_READ_BYTES)).toBe("save");
        expect(saving(MAX_READ_BYTES + 1)).toBe("too-large");
        expect(saving(null)).toBe("save");
        expect(sizeLine(null)).toBe("");
        expect(sizeLine(2_480_000)).toBe("2.4 MB");
    });
});

describe("signed-in sites", () => {
    const now = Date.parse("2026-10-24T09:00:00Z");

    it("splits the sites somebody signed in to from the rest, only when both are there", () => {
        const all = sampleLogins(now);
        const groups = siteGroups(all);
        expect(groups.map((group) => group.label)).toEqual(["Signed in", "Other sites with cookies"]);
        expect(groups[0].sites.map((site) => site.site)).toEqual(["stripe.com", "github.com"]);
        expect(siteGroups(all.filter((site) => !site.signed_in))).toEqual([{ label: null, sites: all.filter((site) => !site.signed_in) }]);
        expect(siteGroups([])).toEqual([]);
    });

    it("names the site before signing the browser out of it", () => {
        expect(signOutAsk("stripe.com")).toEqual({ title: "Sign the browser out of stripe.com?", body: "Any work there will ask you to sign in again." });
    });
});

describe("a sign-in", () => {
    it("says what came of an answer without a dash", () => {
        expect(outcomeWords(false, false).headline).toBe("Not signed in");
        expect(outcomeWords(true, true).headline).toBe("Signed in");
        expect(outcomeWords(true, false).note).toBe("It is carrying on, but the site was still showing a login form just now, so you may be asked again.");
    });

    it("quotes the Lemma's reason as its own", () => {
        expect(reasonLine("The press list is behind a login")).toBe("It says: “The press list is behind a login”");
        expect(reasonLine("  ")).toBeNull();
    });
});

describe("the sample computer", () => {
    const now = Date.parse("2026-10-24T09:00:00Z");
    const tree = sampleTree(now);

    it("waits on the sign-in the sample press chat stopped at", () => {
        const chat = sampleChatOf(SAMPLE_SIGN_IN.conversationId, now);
        const call = chat?.messages.find((message) => message.tool_name === "browser_sign_in");
        expect(call?.tool_call_id).toBe(SAMPLE_SIGN_IN.request.tool_call_id);
        expect((call?.tool_args as { origin?: string }).origin).toBe(SAMPLE_SIGN_IN.request.origin);
        expect(chat?.messages.some((message) => message.kind === "TOOL_RETURN" && message.tool_call_id === SAMPLE_SIGN_IN.request.tool_call_id)).toBe(false);
    });

    it("has a folder for each sample chat it names, with something in it", () => {
        for (const folder of Object.values(sampleChatFolders(now))) {
            expect(tree.get(folder)?.kind).toBe("directory");
            expect(childrenOf(tree, folder).length).toBeGreaterThan(0);
        }
    });

    it("holds something to hide, something too large, an image and more than a page", () => {
        const site = SAMPLE_WORKSPACE + "/repos/acme/site";
        expect(folderRows(childrenOf(tree, site), false).hidden).toBeGreaterThan(0);
        expect([...tree.values()].some((node) => node.size_bytes > MAX_READ_BYTES)).toBe(true);
        expect([...tree.values()].some((node) => node.size_bytes > MAX_TEXT_BYTES && node.size_bytes < MAX_READ_BYTES)).toBe(true);
        expect([...tree.values()].some((node) => node.name.endsWith(".png"))).toBe(true);
        expect(childrenOf(tree, site + "/node_modules").length).toBeGreaterThan(12);
        for (const node of tree.values()) if (node.text !== undefined) expect(node.size_bytes).toBe(utf8Length(node.text));
    });

    it("names only generic people and example domains", () => {
        const text = [...tree.values()].map((node) => node.text ?? "").join("\n");
        for (const email of text.match(/[\w.]+@[\w.-]+/g) ?? []) expect(email).toMatch(/@example\.com$/);
    });
});

describe("the copy", () => {
    const said = [
        WHOSE, SAMPLE_SCREEN, SIGN_IN_FINE, SIGN_IN_NOT_YOURS, TOO_LARGE_TO_SAVE,
        ...(["connecting", "live", "lost", "refused", "signed-out", "no-browser", "unsupported", "stale-image"] as LiveState[]).flatMap((state) => [liveWords(state), signInTrouble(state)]),
        ...[true, false].flatMap((signedIn) => [true, false].map((working) => outcomeWords(signedIn, working).note)),
        startupLine({ state: "downloading" })!.title, startupLine({ state: "starting" })!.title,
        ...["", "/c", "/c/2026-10-24", "/c/2026-10-24/x", "/repos", "/repos/acme", "/repos/acme/site"].map((tail) => whereSay(SAMPLE_WORKSPACE + tail, { home: SAMPLE_HOME, workspace: SAMPLE_WORKSPACE }) ?? ""),
        whereSay(SAMPLE_HOME, { home: SAMPLE_HOME, workspace: SAMPLE_WORKSPACE }) ?? "",
    ].filter(Boolean);

    it("says it the phone's way: no dashes, no exclamations, none of the web's nouns", () => {
        for (const line of said) {
            expect(line).not.toMatch(/—|!/);
            expect(line).not.toMatch(/\b(pod|teammate|workspace|machine|conversation|agent|bot|assistant)s?\b/i);
        }
    });
});
