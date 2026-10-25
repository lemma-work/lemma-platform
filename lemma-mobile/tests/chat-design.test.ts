import { describe, expect, it } from "vitest";
import { approvalDetails } from "@web/thread/approval";
import { withReferences, NOTHING_TYPED } from "../src/thread/attachments";
import { consequenceOf, onlyReads, wordsOf } from "../src/thread/consequence";
import { filesOfMessage, withPhotoRuns } from "../src/thread/message-files";
import { CATCH_UP_TICKS, FADE_FROM, FADE_TICKS, ageFromEnd, arrived, fadeOf, nextShown, splitTail, stillFading, wholeWords, wordsUpTo } from "../src/thread/reveal";
import { stepDetail, stepTimes, tickerLine, tookOf, workedLine } from "../src/thread/steps";
import type { Note, TurnItem } from "@web/thread/turns";

/** The chat's design layer: what is held for a second, how a reply is let
 *  in, the step a run is on, and files drawn as files. */

const approval = (toolArgs: Record<string, unknown>) => consequenceOf(approvalDetails(toolArgs));

describe("which approvals are held down", () => {
    it("holds money moving", () => {
        expect(approval({ title: "Refund $240 to Northwind", tool_name: "stripe_refund", args: { amount: "$240", order: "4471" } })).toBe("money");
        expect(approval({ title: "Pay the courier", tool_name: "run_connector_operation", args: { auth_config: "Stripe", operation: "STRIPE_CREATE_PAYOUT", arguments: { amount: 180 } } })).toBe("money");
        /* A payments connector doing anything that is not reading. */
        expect(approval({ title: "Close Globex", tool_name: "run_connector_operation", args: { operation: "stripe_cancel_subscription_item" } })).toBe("money");
        /* The seeded local chat: a shell command that says it. */
        expect(approval({ title: "Refund $240 to Northwind", tool_name: "exec_command", args: { cmd: "echo refund 240" } })).toBe("money");
    });

    it("holds deleting, in a command, a record, a query or a connector", () => {
        expect(approval({ title: "Clean up", tool_name: "exec_command", args: { cmd: "lemma records delete orders --id 42" } })).toBe("delete");
        expect(approval({ title: "Tidy the build", tool_name: "exec_command", args: { cmd: "rm -rf build/" } })).toBe("delete");
        expect(approval({ title: "Remove the old ticket", tool_name: "pod_write_record", args: { action: "delete", table_name: "tickets", record_id: "T-1" } })).toBe("delete");
        expect(approval({ title: "Drop the scratch table", tool_name: "pod_query", args: { sql: "DROP TABLE scratch" } })).toBe("delete");
        expect(approval({ title: "Archive the thread", tool_name: "run_connector_operation", args: { operation: "GMAIL_DELETE_MESSAGE" } })).toBe("delete");
    });

    it("holds words going outside the Lemma, not a draft of them", () => {
        expect(approval({ title: "Reply to Maria", tool_name: "run_connector_operation", args: { auth_config: "Gmail", operation: "GMAIL_SEND_EMAIL", arguments: { to: "maria@northwind.example" } } })).toBe("outside");
        expect(approval({ title: "Post in #launch", tool_name: "run_connector_operation", args: { operation: "SLACK_CHAT_POST_MESSAGE" } })).toBe("outside");
        expect(approval({ title: "Draft the reply", tool_name: "run_connector_operation", args: { operation: "create_draft", arguments: { to: "maria@northwind.example" } } })).toBeNull();
    });

    it("taps what only reads, whatever its title says", () => {
        expect(approval({ title: "Look up Northwind's refunds", tool_name: "run_connector_operation", args: { operation: "STRIPE_LIST_REFUNDS" } })).toBeNull();
        expect(approval({ title: "Read the post", tool_name: "run_connector_operation", args: { operation: "WORDPRESS_GET_POST" } })).toBeNull();
        expect(onlyReads(wordsOf("GMAIL_FETCH_EMAILS"))).toBe(true);
        expect(onlyReads(wordsOf("GMAIL_SEND_EMAIL"))).toBe(false);
    });

    it("taps the rest: writing a file, adding a record, Lemma's own messages inside it", () => {
        expect(approval({ title: "Save the notes", tool_name: "pod_write_file", args: { path: "/notes.md", content: "…" } })).toBeNull();
        expect(approval({ title: "Add the ticket", tool_name: "pod_write_record", args: { action: "create", table_name: "tickets", data: { subject: "Lamp" } } })).toBeNull();
        expect(approval({ title: "Tell Sam", tool_name: "channel_post", args: { to: "Sam", text: "Send the invoice" } })).toBeNull();
        expect(approval({ title: "Run the tests", tool_name: "exec_command", args: { cmd: "npm test" } })).toBeNull();
    });

    it("reads a coding agent's own request by its command", () => {
        expect(approval({ title: "Bash", tool_name: "Bash", agent_host_permission: { input: { command: "rm -rf node_modules" }, options: [] } })).toBe("delete");
        expect(approval({ title: "Edit", tool_name: "Edit", agent_host_permission: { input: { file_path: "a.ts" }, options: [] } })).toBeNull();
    });

    it("falls back to the title only for a runner that names nothing else", () => {
        expect(approval({ title: "Send the invoice to Globex", tool_name: "run_connector_operation", args: {} })).toBe("money");
        expect(approval({ title: "Delete the duplicates", tool_name: "", args: {} })).toBe("delete");
    });
});

describe("a reply let in word by word", () => {
    it("waits for a word still being written", () => {
        expect(wholeWords("Their order had 4 la", true)).toBe(4);
        expect(wholeWords("Their order had 4 lamps ", true)).toBe(5);
        expect(wholeWords("Their order had 4 la", false)).toBe(5);
        expect(wholeWords("", true)).toBe(0);
    });

    it("catches up within a few ticks however big the chunk", () => {
        let shown = 0;
        let ticks = 0;
        while (shown < 400) {
            shown = nextShown(shown, 400);
            ticks += 1;
        }
        expect(ticks).toBeLessThanOrEqual(CATCH_UP_TICKS * 6);
        expect(nextShown(10, 11)).toBe(11);
        expect(nextShown(12, 11)).toBe(11);
    });

    it("cuts the text after a word, leaving the space off", () => {
        expect(wordsUpTo("One two  three four", 3)).toBe("One two  three");
        expect(wordsUpTo("One two", 9)).toBe("One two");
        expect(wordsUpTo("One two", 0)).toBe("");
    });

    it("fades a word in over a few ticks, newest faintest", () => {
        expect(fadeOf(0)).toBe(FADE_FROM);
        expect(fadeOf(FADE_TICKS)).toBe(1);
        expect(fadeOf(FADE_TICKS / 2)).toBeGreaterThan(FADE_FROM);
        let log = arrived([], 1, 3);
        log = arrived(log, 2, 2);
        /* The two newest came at tick 2, the three before at tick 1. */
        expect(ageFromEnd(0, log, 3)).toBe(1);
        expect(ageFromEnd(1, log, 3)).toBe(1);
        expect(ageFromEnd(2, log, 3)).toBe(2);
        expect(ageFromEnd(9, log, 3)).toBe(FADE_TICKS);
        expect(stillFading(log, 3)).toBe(true);
        expect(stillFading(log, 2 + FADE_TICKS)).toBe(false);
        /* What has finished fading is let go. */
        expect(arrived(log, 1 + FADE_TICKS, 1).length).toBe(2);
    });

    it("splits at the last paragraph, never inside code", () => {
        expect(splitTail("First.\n\nSecond, still go")).toEqual({ settled: "First.\n\n", tail: "Second, still go" });
        expect(splitTail("Only one line")).toEqual({ settled: "", tail: "Only one line" });
        const code = "Here:\n\n```\nlet a = 1;\n\nlet b = 2;";
        expect(splitTail(code)).toEqual({ settled: "Here:\n\n", tail: "```\nlet a = 1;\n\nlet b = 2;" });
    });
});

describe("the step a run is on", () => {
    const note = (one: Partial<Note>): Note => ({ kind: "tool", label: "Read file", detail: "", ...one });

    it("says the agent's own sentence first, then the step and what it is on", () => {
        expect(tickerLine([note({ detail: "Read order 4471 in Stripe", said: true })], false)).toBe("Read order 4471 in Stripe");
        expect(tickerLine([note({ detail: "/policies/refunds.md" })], false)).toBe("Read file · /policies/refunds.md");
        expect(tickerLine([note({ kind: "thought", label: "Thought", detail: "Four lamps…" })], false)).toBe("Thinking");
        expect(tickerLine([], false)).toBe("Starting");
        expect(tickerLine([note({})], true)).toBe("Writing");
    });

    it("leaves out a detail that is only the names of the arguments", () => {
        expect(tickerLine([note({ label: "Pod query", detail: "sql" })], false)).toBe("Pod query");
        expect(stepDetail(note({ detail: "full_page, instructions" }))).toBe("");
        expect(stepDetail(note({ detail: "SELECT 1" }))).toBe("SELECT 1");
        expect(stepDetail(note({ detail: "auth_config" }))).toBe("");
        /* One plain word may be what was searched for. */
        expect(stepDetail(note({ detail: "refunds" }))).toBe("refunds");
        /* The agent's own words are kept, however they look. */
        expect(stepDetail(note({ detail: "checking", said: true }))).toBe("checking");
    });

    it("folds into how long and how many", () => {
        expect(workedLine("1m 6s", 5)).toBe("Worked for 1m 6s · 5 steps");
        expect(workedLine("", 1)).toBe("1 step");
    });

    it("times each step from its call to its return", () => {
        const times = stepTimes([
            { kind: "TOOL_CALL", tool_call_id: "a", created_at: "2026-10-24T10:00:00Z" },
            { kind: "TOOL_RETURN", tool_call_id: "a", created_at: "2026-10-24T10:00:03.4Z" },
            { kind: "TOOL_CALL", tool_call_id: "b", created_at: "2026-10-24T10:00:04Z" },
            { kind: "TOOL_RETURN", tool_call_id: "b", created_at: "2026-10-24T10:01:10Z" },
            { kind: "TOOL_CALL", tool_call_id: "c", created_at: "2026-10-24T10:01:11Z" },
        ]);
        expect(tookOf(times.get("a"))).toBe("3s");
        expect(tookOf(times.get("b"))).toBe("1m 6s");
        expect(tookOf(times.get("c"))).toBe("");
        expect(tookOf({ startMs: 0, endMs: 400 })).toBe("<1s");
    });
});

describe("files in a message", () => {
    it("draws a person's attachments as files and the words above them as the message", () => {
        const sent = withReferences("Photos attached.", [{ name: "lamp.jpg", path: "/me/c/2026-10-24/refund/lamp.jpg" }, { name: "Box label.png", path: "/me/c/2026-10-24/refund/Box label.png" }]);
        expect(filesOfMessage(sent)).toEqual({ text: "Photos attached.", files: [{ name: "lamp.jpg", path: "/me/c/2026-10-24/refund/lamp.jpg" }, { name: "Box label.png", path: "/me/c/2026-10-24/refund/Box label.png" }] });
        /* Nothing typed: the files say it. */
        expect(filesOfMessage(withReferences(NOTHING_TYPED, [{ path: "/me/a.pdf" }])).text).toBe("");
        expect(filesOfMessage("Just words.")).toEqual({ text: "Just words.", files: [] });
        /* Not this app's list: left as it was written. */
        const other = "Personal files available to this run:\nsee the folder";
        expect(filesOfMessage(other)).toEqual({ text: other, files: [] });
    });

    it("groups pictures shown one after another, and only those", () => {
        const picture = (id: string, path: string): TurnItem => ({ kind: "resource", id, toolCallId: "call-" + id, resource: { type: "FILE", path } as never });
        const said: TurnItem = { kind: "text", id: "t", text: "Here they are.", at: "" };
        const drawn = withPhotoRuns([said, picture("a", "/x/1.jpg"), picture("b", "/x/2.png"), picture("c", "/x/3.webp"), picture("d", "/x/brief.pdf"), picture("e", "/x/4.jpg")]);
        expect(drawn.map((one) => one.kind)).toEqual(["text", "photos", "resource", "resource"]);
        const grid = drawn[1];
        expect(grid.kind === "photos" ? grid.paths : []).toEqual(["/x/1.jpg", "/x/2.png", "/x/3.webp"]);
    });
});
