import { describe, expect, it } from "vitest";
import { Lexer } from "marked";
import { sampleChatOf } from "../src/areas/chat/sample-chats";
import { NOTHING_TYPED, canSend, contentFor, isAlreadyUploaded, markAttachment, offered, toAttachments, uploadFailure, withReferences } from "../src/thread/attachments";
import { blocksOf, withoutTags } from "../src/thread/blocks";
import { channelThread, rememberReplyMode, rememberedReplyMode, replyChoices, replyModeFor, sendMetadata } from "@web/thread/channel-reply";
import { composerNote, mergeIndex, queuedNote, stateOf, threadOf } from "../src/thread/derive";
import { draftKeyFor, readDraft, writeDraft } from "@web/thread/drafts";
import { holdFiles, takeFiles } from "../src/thread/hand-over";
import type { KeyValueStore } from "../src/thread/kept";
import { needsAiModel, runFailure } from "@web/thread/model-setup";
import { pendingSend } from "@web/thread/pending-sends";
import { dataPreview, excerptOf, fileKind, formatOf, readableSize, withoutFrontmatter } from "../src/thread/previews";
import { modelChoices, readChoice, readRuntime, runtimePayload, shortModel } from "../src/thread/runtimes";
import { runLooksStuck, STUCK_AFTER_MS } from "@web/thread/stuck-run";
import { runFailure as describeRunFailure } from "@web/thread/transcript-state";
import { UNREACHABLE, isTransportFailure, saidAboutSending } from "../src/thread/trouble";
import { buildTurns, type RawMessage } from "@web/thread/turns";

function memory(): KeyValueStore & { size: () => number } {
    const held = new Map<string, string>();
    return {
        getItem: (key) => held.get(key) ?? null,
        setItem: (key, value) => void held.set(key, value),
        removeItem: (key) => void held.delete(key),
        size: () => held.size,
    };
}

const file = (name: string, size: number | null = 1000, type = "image/jpeg") => ({ uri: "file:///cache/" + name, name, type, size });

describe("attachments, as the web words them", () => {
    it("names each uploaded file at the end of the message", () => {
        expect(withReferences("Look at these", [{ name: "lamp.jpg", path: "/me/conversations/c1/lamp.jpg" }, { path: "/me/conversations/c1/receipt.pdf" }]))
            .toBe("Look at these\n\nPersonal files available to this run:\n- lamp.jpg: /me/conversations/c1/lamp.jpg\n- receipt.pdf: /me/conversations/c1/receipt.pdf");
        expect(withReferences("Nothing attached", [])).toBe("Nothing attached");
    });

    it("sends a sentence when only files were attached", () => {
        const held = toAttachments([file("lamp.jpg")]);
        expect(contentFor("  ", held)).toBe(NOTHING_TYPED);
        expect(contentFor(" hi ", held)).toBe("hi");
        expect(canSend("", held)).toBe(true);
        expect(canSend(" ", [])).toBe(false);
    });

    it("refuses what is too large before spending an upload on it", () => {
        const { take, refused } = offered([file("small.jpg", 2_000), file("film.mov", 200 * 1024 * 1024, "video/quicktime")]);
        expect(take.map((one) => one.name)).toEqual(["small.jpg"]);
        expect(refused).toBe("film.mov is too large to attach (200 MB).");
        expect(offered([file("a.mov", 2e8), file("b.mov", 2e8)]).refused).toBe("2 files are too large to attach.");
    });

    it("keys two copies of one file apart, and keeps an uploaded one for a retry", () => {
        const [first, second] = toAttachments([file("lamp.jpg"), file("lamp.jpg")]);
        expect(first.key).not.toBe(second.key);
        const marked = markAttachment([first, second], first.key, { status: "uploaded", path: "/me/c/lamp.jpg" });
        expect(isAlreadyUploaded(marked[0])).toBe(true);
        expect(isAlreadyUploaded(marked[1])).toBe(false);
        expect(uploadFailure("lamp.jpg", "The file is empty.")).toBe("Couldn’t upload lamp.jpg. The file is empty.");
    });

    it("hands files from one screen's box to the chat it opens, once", () => {
        const name = holdFiles([file("a.jpg")]);
        expect(name).not.toBeNull();
        expect(takeFiles(name).map((one) => one.name)).toEqual(["a.jpg"]);
        expect(takeFiles(name)).toEqual([]);
        expect(holdFiles([])).toBeNull();
    });
});

describe("drafts kept on the phone", () => {
    it("keeps one per chat and drops an emptied one", () => {
        const store = memory();
        writeDraft(store, draftKeyFor("pod", "c1"), "half a thought");
        writeDraft(store, draftKeyFor("pod", null), "for a new chat");
        expect(readDraft(store, "pod:c1")).toBe("half a thought");
        expect(readDraft(store, "pod:new")).toBe("for a new chat");
        writeDraft(store, "pod:c1", "   ");
        expect(readDraft(store, "pod:c1")).toBe("");
    });

    it("keeps the forty most recent", () => {
        const store = memory();
        for (let index = 0; index < 45; index += 1) writeDraft(store, "pod:c" + index, "draft " + index);
        expect(readDraft(store, "pod:c0")).toBe("");
        expect(readDraft(store, "pod:c44")).toBe("draft 44");
        expect(readDraft(store, "pod:c5")).toBe("draft 5");
    });
});

describe("where a message goes in a chat that lives on a platform too", () => {
    it("reads the platform off the conversation, and nothing for a plain chat", () => {
        expect(channelThread({})).toBeNull();
        const email = channelThread({ surface_platform: "RESEND", conversation_kind: "EMAIL", channel_name: "Globex" });
        expect(email).toMatchObject({ platform: "RESEND", kind: "EMAIL", place: "Globex" });
        expect(channelThread({ surface_platform: "slack", channel_name: "launch" })?.place).toBe("#launch");
    });

    it("starts a person's own thread on the reply and a group on the note", () => {
        const email = channelThread({ surface_platform: "RESEND", conversation_kind: "EMAIL" })!;
        const group = channelThread({ surface_platform: "TELEGRAM", conversation_kind: "CHANNEL", channel_name: "Design partners" })!;
        expect(replyModeFor(email, null)).toBe("reply");
        expect(replyModeFor(group, null)).toBe("note");
        expect(replyChoices(email, "Renewals").map((one) => one.label)).toEqual(["Note to Renewals", "Reply by email"]);
        expect(replyChoices(group, "Renewals")[1].hint).toBe("Renewals’s answer goes to the group.");
        expect(sendMetadata(group, "note")).toEqual({ private_note: true });
        expect(sendMetadata(group, "reply")).toBeUndefined();
    });

    it("remembers the choice made in each chat", () => {
        const store = memory();
        rememberReplyMode(store, "c1", "reply");
        expect(rememberedReplyMode(store, "c1")).toBe("reply");
        expect(rememberedReplyMode(store, "c2")).toBeNull();
    });
});

describe("the model picker's rule", () => {
    const runtime = readRuntime({
        id: "r1",
        name: "Anthropic",
        default_model_name: "anthropic/models/claude-sonnet-5-5",
        model_catalog: [{ name: "anthropic/models/claude-sonnet-5-5", display_name: "Sonnet 5.5" }, { name: "anthropic/models/claude-opus-5-5" }],
    })!;

    it("reads a runtime and a choice", () => {
        expect(runtime.models.map((model) => model.label)).toEqual(["Sonnet 5.5", "claude-opus-5-5"]);
        expect(readRuntime({ id: "r2", default_model_name: "openai/models/gpt-5" })!.models).toEqual([{ name: "openai/models/gpt-5", label: "gpt-5" }]);
        expect(readRuntime({ id: "r3", status: "DISABLED" })!.archived).toBe(true);
        expect(readChoice({ profile_id: "r1", model_name: null })).toEqual({ runtimeId: "r1", model: "" });
        expect(readChoice({})).toBeNull();
        expect(shortModel("openai/routers/fast/")).toBe("fast");
    });

    it("offers the models of what the chat runs on when nothing is chosen, default first", () => {
        const offer = modelChoices([runtime], null, { runtimeId: "r1", model: "" }, null);
        expect(offer.offered).toBe(true);
        expect(offer.defaultLabel).toBe("Default (Sonnet 5.5)");
        expect(modelChoices([runtime], { runtimeId: "r1", model: "anthropic/models/claude-opus-5-5" }, null, null).defaultLabel).toBe("Default (claude-opus-5-5)");
        expect(modelChoices([runtime], null, { runtimeId: "r1", model: "" }, { runtimeId: "r1", model: "anthropic/models/claude-sonnet-5-5" }).chosenLabel).toBe("Sonnet 5.5");
        expect(modelChoices([], null, null, null)).toMatchObject({ offered: false, defaultLabel: "Default (no model set)" });
        expect(modelChoices([{ ...runtime, archived: true }], null, { runtimeId: "r1", model: "" }, null).offered).toBe(false);
    });

    it("sends the choice whole, null to inherit again", () => {
        expect(runtimePayload({ runtimeId: "r1", model: "" })).toEqual({ profile_id: "r1", model_name: null });
        expect(runtimePayload(null)).toBeNull();
    });
});

describe("what a chat shows of a file or a table", () => {
    it("knows a file by its type and its extension", () => {
        expect(fileKind("", "/Notes/Brief.pdf")).toBe("pdf");
        expect(fileKind("application/octet-stream", "/clips/demo.mp4")).toBe("video");
        expect(fileKind("", "/policies/refunds.md")).toBe("markdown");
        expect(fileKind("image/png", "/a")).toBe("image");
        expect(fileKind("", "/data.csv")).toBe("text");
        expect(fileKind("", "/archive.zip")).toBe("binary");
        expect(formatOf("archive.zip")).toBe("ZIP");
        expect(readableSize(48_213)).toBe("47 KB");
    });

    it("shows the start of the words, cut at a line", () => {
        const long = Array.from({ length: 60 }, (_, index) => "Line number " + index + " of the report.").join("\n");
        const excerpt = excerptOf(long, 200);
        expect(excerpt.cut).toBe(true);
        expect(excerpt.text.endsWith("…")).toBe(true);
        expect(excerpt.text.length).toBeLessThanOrEqual(201);
        expect(excerptOf("short").cut).toBe(false);
        expect(withoutFrontmatter("---\nname: refunds\n---\n# Refunds")).toBe("# Refunds");
    });

    it("previews eight rows and six columns, and says what it leaves out", () => {
        const rows = Array.from({ length: 12 }, (_, index) => ({ a: index, b: "x", c: null, d: { nested: true }, e: 1, f: 2, g: 3, h: 4 }));
        const preview = dataPreview(rows);
        expect(preview.columns).toEqual(["a", "b", "c", "d", "e", "f"]);
        expect(preview.rows).toHaveLength(8);
        expect(preview.rows[0].slice(0, 4)).toEqual(["0", "x", "", "{\"nested\":true}"]);
        expect(preview.footnote).toBe("8 of 12 rows · 2 more columns");
        expect(dataPreview([{ a: 1 }]).footnote).toBe("");
        expect(dataPreview([{ a: 1 }], true).capped).toBe(true);
    });
});

describe("markdown, block by block", () => {
    it("keeps ordinary blocks together and gives code, tables and quotes a copy", () => {
        const text = "Here is the plan.\n\n- one\n- two\n\n```sh\nnpm run check\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n> quoted\n\nThe end.";
        const blocks = blocksOf(Lexer.lex(text));
        expect(blocks.map((block) => block.copyLabel)).toEqual([null, "Copy code", "Copy table", "Copy quote", null]);
        expect(blocks[1].copy).toBe("npm run check");
        expect(blocks[2].copy).toBe("| a | b |\n| - | - |\n| 1 | 2 |");
        expect(blocks[0].source).toContain("- two");
        /* Nothing lost but the blank lines between blocks. */
        expect(blocks.map((block) => block.source).join("").replace(/\s+/g, "")).toBe(text.replace(/\s+/g, ""));
    });

    it("reads an agent's HTML as its words", () => {
        expect(withoutTags("<div style=\"color:red\">Status: <b>green</b></div><br><details><summary>More</summary>Fine &amp; well</details>"))
            .toBe("Status: green\n\nMore\nFine & well");
    });

    it("leaves no tag behind, and reads an entity once", () => {
        expect(withoutTags("<scr<b>ipt>alert(1)</script>")).toBe("alert(1)");
        expect(withoutTags("a <b and c")).toBe("a b and c");
        expect(withoutTags("&amp;lt;b&amp;gt; &lt;i&gt;")).toBe("&lt;b&gt; <i>");
    });
});

describe("what the thread draws", () => {
    const at = (seconds: number) => new Date(Date.UTC(2026, 9, 24, 9, 0, seconds)).toISOString();
    const messages: RawMessage[] = [
        { id: "u1", role: "user", kind: "TEXT", content: "Refund Northwind?", sequence: 1, created_at: at(0) },
        { id: "t1", role: "assistant", kind: "TOOL_CALL", tool_name: "request_approval", tool_args: { title: "Refund $240", reason: "order 4471" }, tool_call_id: "call-1", sequence: 2, created_at: at(5) },
    ];

    it("reads a status as the thread's state", () => {
        expect(stateOf("RUNNING")).toBe("running");
        expect(stateOf("stop_requested")).toBe("running");
        expect(stateOf("WAITING")).toBe("waiting");
        expect(stateOf("FAILED")).toBe("failed");
        expect(stateOf(undefined)).toBe("idle");
    });

    it("docks the pause the run is stopped on", () => {
        const thread = threadOf({ messages, pending: [], running: false, withdrawn: new Set(), notes: [], previous: [] });
        expect(thread.waitingOn?.id).toBe("call-1");
        expect(thread.waitingOn?.details.canApproveForSession).toBe(true);
        expect(composerNote({ running: false, winding: false, state: "waiting", waitingOn: thread.waitingOn, signingIn: null })).toBe("sending a message skips this request");
        expect(composerNote({ running: false, winding: false, state: "waiting", waitingOn: null, signingIn: null })).toBe("waiting on you");
        expect(composerNote({ running: true, winding: true, state: "running", waitingOn: null, signingIn: null })).toBe("stopping…");
    });

    it("draws a send before the server has it, and keeps a refused one with its words", () => {
        const going = pendingSend("And email Maria", "send", messages);
        const sending = threadOf({ messages, pending: [going], running: false, withdrawn: new Set(), notes: [], previous: [] });
        expect(sending.turns.at(-1)?.human).toMatchObject({ text: "And email Maria", pending: "sending" });
        const failed = threadOf({ messages, pending: [{ ...going, state: "failed" }], running: false, withdrawn: new Set(), notes: [], previous: [] });
        expect(failed.turns.at(-1)?.human?.pending).toBe("failed");
    });

    it("holds what was said mid-run in the tray, until it is taken back", () => {
        const held: RawMessage[] = [...messages, { id: "q1", role: "user", kind: "TEXT", content: "Also the receipt", sequence: 3, created_at: at(9), metadata: { during_active_run: true } }];
        const running = threadOf({ messages: held, pending: [], running: true, withdrawn: new Set(), notes: [], previous: [] });
        expect(running.queued).toEqual([{ id: "q1", text: "Also the receipt", withdrawable: true }]);
        expect(queuedNote(running.queued, "Support")).toBe("Support hears this as soon as the work in progress allows");
        const gone = threadOf({ messages: held, pending: [], running: true, withdrawn: new Set(["q1"]), notes: [], previous: [] });
        expect(gone.queued).toEqual([]);
    });

    it("keeps the turns that did not change, so their rows are not drawn again", () => {
        const first = threadOf({ messages, pending: [], running: false, withdrawn: new Set(), notes: [], previous: [] });
        const again = threadOf({ messages: [...messages], pending: [], running: false, withdrawn: new Set(), notes: [], previous: first.turns });
        expect(again.turns).toBe(first.turns);
    });

    it("lays a runtime note onto its turn", () => {
        const thread = threadOf({ messages, pending: [], running: false, withdrawn: new Set(), notes: [{ text: "The model was changed.", turnId: "u1" }], previous: [] });
        expect(thread.turns[0].runtimeNotes).toEqual(["The model was changed."]);
    });

    it("draws a run in flight into the last turn", () => {
        expect(mergeIndex([], true, true)).toBe(-1);
        const turns = buildTurns(messages);
        expect(mergeIndex(turns, false, true)).toBe(turns.length - 1);
        expect(mergeIndex(turns, false, false)).toBe(-1);
    });
});

describe("a run's failure, in words the reader can act on", () => {
    it("says no model by its code, without Try again", () => {
        const record = { last_run_error: "No AI model is set up.", last_run_error_code: "model_not_configured", last_run_retryable: false };
        expect(runFailure("failed", null, record)).toEqual({ message: "No AI model is set up.", noModel: true, retryable: false });
        expect(needsAiModel({ code: "model_not_configured" })).toBe(true);
        expect(runFailure("failed", null, { last_run_error: "boom" }).retryable).toBe(true);
    });

    it("says a coding agent's failure as what happened", () => {
        expect(describeRunFailure("HOST_WAIT_TIMEOUT").codingAgents).toBe(true);
        expect(describeRunFailure("").text).toBe("That run failed.");
        expect(describeRunFailure("The calendar refused it.")).toEqual({ text: "The calendar refused it.", codingAgents: false });
    });

    it("offers a reload once a run has been quiet too long", () => {
        expect(runLooksStuck("running", false, STUCK_AFTER_MS)).toBe(true);
        expect(runLooksStuck("running", true, STUCK_AFTER_MS)).toBe(false);
        expect(runLooksStuck("idle", false, STUCK_AFTER_MS * 2)).toBe(false);
    });

    it("says a send that could not reach the server in the app's words", () => {
        expect(saidAboutSending(new TypeError("Network request failed"), "That did not send.")).toBe(UNREACHABLE);
        expect(saidAboutSending({ status: 500, rawResponse: { message: "The Lemma is paused." } }, "That did not send.")).toBe("The Lemma is paused.");
        expect(saidAboutSending({ status: 500 }, "That did not send.")).toBe("That did not send.");
        expect(isTransportFailure({ status: 503 })).toBe(true);
        expect(isTransportFailure({ status: 400 })).toBe(false);
    });
});

describe("the sample chats", () => {
    const now = Date.UTC(2026, 9, 24, 12);
    const kinds = (id: string) => {
        const chat = sampleChatOf(id, now)!;
        const turns = buildTurns([...chat.older, ...chat.messages]);
        return {
            chat,
            turns,
            cards: new Set(turns.flatMap((turn) => turn.notes.flatMap((note) => (note.card ? [note.card.kind] : [])))),
            items: new Set(turns.flatMap((turn) => turn.items.map((item) => (item.kind === "resource" ? "resource:" + item.resource.type : item.kind)))),
        };
    };

    it("carry every tool card between them", () => {
        const all = new Set([...kinds("support-tone").cards, ...kinds("support-refund").cards, ...kinds("launch-copy").cards]);
        for (const kind of ["sources", "terminal", "browser", "image", "file-read", "file-change", "task", "ask", "connector", "wait"]) expect(all).toContain(kind);
    });

    it("carry every resource, a memory note, a notice and a relayed answer", () => {
        const refund = kinds("support-refund");
        const tone = kinds("support-tone");
        for (const item of ["resource:WIDGET", "resource:FILE", "resource:TABLE", "interaction"]) expect(refund.items).toContain(item);
        for (const item of ["resource:APP", "resource:BROWSER", "resource:WORKFLOW", "tool-card"]) expect(tone.items).toContain(item);
        expect(refund.turns.some((turn) => turn.noted?.length)).toBe(true);
        expect(refund.turns.filter((turn) => turn.day).length).toBeGreaterThan(1);
        expect(tone.turns.some((turn) => turn.notice)).toBe(true);
        expect(tone.turns.some((turn) => turn.human?.answeredBy)).toBe(true);
        expect(tone.turns.some((turn) => turn.human?.note)).toBe(true);
    });

    it("cover the states a run can be in", () => {
        expect(kinds("launch-day").turns.at(-1)?.items.at(-1)).toMatchObject({ kind: "interaction", interaction: { open: true } });
        expect(kinds("launch-day").chat.messages.find((one) => one.tool_name === "ask_user")).toBeTruthy();
        expect(kinds("launch-press").turns.at(-1)?.items.at(-1)).toMatchObject({ kind: "tool-card", card: { kind: "sign-in", resolved: false } });
        expect(kinds("hiring-old").chat.status).toBe("FAILED");
        expect(runFailure("failed", null, kinds("books-close").chat.conversation as never).noModel).toBe(true);
        expect(kinds("books-close").chat.older.length).toBeGreaterThan(0);
        expect(channelThread(kinds("renewals-globex").chat.conversation.metadata as Record<string, unknown>)?.kind).toBe("EMAIL");
        expect(sampleChatOf("not-a-chat", now)).toBeNull();
    });
});
