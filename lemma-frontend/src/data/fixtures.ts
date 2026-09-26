import { key } from "@/session/storage";
import { NEW_CONVERSATION } from "./types";
import type { Conversation, FileContent, Invitation, Member, Message, NewOrg, Org, Profile, Pod, PodSource, Surface, Tab } from "./types";
import { displayAgentName } from "./agent-names";
import { agentChanges, agentRows, readAgentDetail, type AgentDraft } from "./agents";
import {
    createRequest,
    readRun,
    readRuns,
    readSchedule,
    readSchedules,
    type ScheduleDraft,
} from "@/schedule/schedules";
import { byEffort, readConnectable, type Connectable } from "./connectable";
import type { JoinPolicy, JoinRequest, OrgJoin } from "./joining";
import { capabilityList, POD_DEFAULT_TOOLSETS } from "@/stage/colleagues";
import { readAccount, readConnector, type Connector, type ConnectorAccount } from "./accounts";
import {
    readChoice,
    readComputer,
    readLocalAgent,
    readRuntime,
    type Computer,
    type LocalAgent,
    type Runtime,
} from "./runtimes";
import { SAMPLE_TABLES, sampleShape, sampleTable } from "./sample-tables";

/** A stand-in for when there is no session — enough to judge the layout, and
 *  loudly labelled so it is never mistaken for your pods. */

const ORGS: Org[] = [{ id: "acme", name: "Acme" }];

/** A first morning, on demand.
 *
 *  The arrival screen only ever shows to an account that belongs to nothing,
 *  which is a state this app cannot otherwise reach: sample mode has an
 *  organization, and a live one needs a genuinely new account. So the sample
 *  source can be told to have none — `localStorage["lemma-app:sample-orgs"] =
 *  "none"` — in the same spirit as the switch that selects this source in the
 *  first place. It is scaffolding for judging a screen, and it lives here,
 *  where scaffolding for judging screens belongs. */
function belongsToNothing(): boolean {
    try {
        return localStorage.getItem(key("sample-orgs")) === "none";
    } catch {
        return false;
    }
}

/** One of each rung the arrival screen has to draw: an invitation that names a
 *  teammate, so the best case is walkable, and one that names only an
 *  organization. */
const INVITATIONS: Invitation[] = [
    {
        id: "invite-acme",
        orgId: "acme",
        orgName: "Acme",
        podId: "roundtable",
        podName: "roundtable",
        podAbout: "Where the standing decisions get made and written down",
        role: "ORG_MEMBER",
    },
    {
        id: "invite-northfield",
        orgId: "northfield",
        orgName: "Northfield",
        podId: null,
        podName: null,
        podAbout: null,
        role: "ORG_EDITOR",
    },
];

const SUGGESTED: Org[] = [{ id: "northfield", name: "Northfield" }];

/** Once the sample account belongs somewhere, it stops being a new one.
 *
 *  Without this, `listOrgs` keeps answering with nothing and the arrival
 *  screen reappears on top of the organization that was just joined — which
 *  would make the one screen this scaffolding exists to show impossible to
 *  leave. */
function stopPretending(): void {
    try {
        localStorage.removeItem(key("sample-orgs"));
    } catch {
        /* nothing was stored, so nothing is pretending */
    }
}

const MEMBERS: Member[] = [
    { id: "you", name: "You", initials: "DJ", kind: "person", role: "Owner", can: "everything" },
    { id: "priya", name: "Priya", initials: "PR", kind: "person", role: "Member", can: "approves customer quotes" },
    { id: "lemma", name: "Marketing", initials: "MA", kind: "teammate", role: "Teammate", can: "can draft · cannot send" },
];

/* Eleven, because that is what a real roster looks like — a sidebar that
   only ever renders one row cannot be judged. */
const PODS: Pod[] = [
    {
        id: "marketing",
        orgId: "acme",
        name: "Marketing",
        iconUrl: "/teammates/loop-v1.png",
        teammate: { name: "Marketing", initials: "MA", iconUrl: "/teammates/loop-v1.png" },
        subtitle: "with Priya and you",
        members: MEMBERS,
        waiting: "1 decision waiting",
    },
    {
        id: "personal",
        orgId: "acme",
        name: "Personal",
        iconUrl: "/teammates/pleat-v1.png",
        teammate: { name: "Personal", initials: "PE", iconUrl: "/teammates/pleat-v1.png" },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "panini",
        orgId: "acme",
        name: "panini",
        iconUrl: "/teammates/frame-v1.png",
        teammate: { name: "panini", initials: "PA", iconUrl: "/teammates/frame-v1.png" },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "sidekick",
        orgId: "acme",
        name: "sidekick",
        iconUrl: "🖥️",
        teammate: { name: "sidekick", initials: "SI", iconUrl: "🖥️" },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "roundtable",
        orgId: "acme",
        name: "roundtable",
        iconUrl: "🪑",
        teammate: { name: "roundtable", initials: "RO", iconUrl: "🪑" },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "lemma-design",
        orgId: "acme",
        name: "Lemma Design",
        iconUrl: null,
        teammate: { name: "Lemma Design", initials: "LE", iconUrl: null },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "lemma-motion",
        orgId: "acme",
        name: "lemma-motion",
        iconUrl: null,
        teammate: { name: "lemma-motion", initials: "LE", iconUrl: null },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "nachiketa",
        orgId: "acme",
        name: "Nachiketa",
        iconUrl: null,
        teammate: { name: "Nachiketa", initials: "NA", iconUrl: null },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "memory-bench",
        orgId: "acme",
        name: "memory-bench",
        iconUrl: "💭",
        teammate: { name: "memory-bench", initials: "ME", iconUrl: "💭" },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "launch-craft-library",
        orgId: "acme",
        name: "launch-craft-library",
        iconUrl: null,
        teammate: { name: "launch-craft-library", initials: "LA", iconUrl: null },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
    {
        id: "ap-desk",
        orgId: "acme",
        name: "ap-desk",
        iconUrl: "💼",
        teammate: { name: "ap-desk", initials: "AP", iconUrl: "💼" },
        subtitle: "just you",
        members: MEMBERS,
        waiting: "",
    },
];

/* Raw-message shaped, exactly like the API — so the sample and the live path
   go through the same turn builder and the same transcript. A fixture that
   skips the parser is a fixture that cannot catch a parser bug. */
const EARLIER_ASKS = [
    "where are we on the competitor sweep?",
    "can you redo the headline?",
    "what did Priya say about the pricing page?",
    "pull the numbers for last week",
];

const EARLIER_REPLIES = [
    "Swept all five. Only Northfield moved — they dropped the free tier.",
    "Rewritten. It leads with the customer now instead of the product.",
    "She wants the per-seat line gone before it goes out.",
    "Pulled. Signups are flat; the trial-to-paid rate is up four points.",
];

const CONVERSATION: Conversation = {
    id: "fixture",
    title: "Monday launch",
    status: "COMPLETED",
    messages: [
        {
            id: "m1",
            role: "user",
            kind: "TEXT",
            sequence: 1,
            created_at: new Date().toISOString(),
            text: "Where did v3 land?",
        },
        {
            id: "m2",
            role: "assistant",
            kind: "THINKING",
            sequence: 2,
            created_at: new Date().toISOString(),
            text: "Priya is right that the quote is the strongest line in here.",
        },
        {
            id: "m3",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 3,
            created_at: new Date().toISOString(),
            tool_name: "write",
            tool_args: { path: "monday-launch-v3.md" },
        },
        {
            id: "m4",
            role: "assistant",
            kind: "TEXT",
            sequence: 4,
            created_at: new Date().toISOString(),
            text: [
                "The customer story is stronger. I'm moving it to the **opening**.",
                "",
                "## What changed in v3",
                "",
                "- Jordan Kim's quote now opens the piece, ahead of the product line",
                "- The headline lost *\"a more human way to work\"* — it was doing no work",
                "- Cut the third paragraph entirely",
                "",
                "Two things still need a person: the quote is `unapproved`, and the",
                "design-partner list is Maya's.",
                "",
                '<div style="border-left:4px solid #6c63ff; padding:10px 14px; background:#f4f3ff; border-radius:6px;"><strong>Status</strong> — draft ready, one approval outstanding.</div>',
            ].join("\n"),
        },
        /* The work itself, for the tools this app reads rather than
           summarises. Same reason as the pauses below: a terminal block, a
           list of sources and a sleeping run could only be looked at by
           having a live agent do those things, which meant nobody ever looked
           at them. Every shape here is the backend's own — `stdout`/`stderr`
           and `exit_code` from `ExecCommandResult`, `results` from
           `WebSearchResponse`, `{ result }` from a connector, `woke_because`
           from `SnoozeResponse`.

           A stack of them in one turn is not padding. It is the case that
           actually breaks: seven cards between two things the teammate said,
           which is what a real run looks like and what the closed state has
           to survive. */
        {
            id: "t1",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.1,
            created_at: new Date().toISOString(),
            tool_name: "exec_command",
            tool_args: { cmd: "wc -w drafts/monday-launch-v3.md", workdir: "/workspace/launch" },
            tool_call_id: "call_wc",
        },
        {
            id: "t1r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.11,
            created_at: new Date().toISOString(),
            tool_call_id: "call_wc",
            tool_result: {
                success: true,
                completed: true,
                exit_code: 0,
                stdout: "     412 drafts/monday-launch-v3.md\n",
                stderr: "",
            },
        },
        /* A non-zero exit, which is the state worth having in the sample and
           the one that is easiest to get wrong. The card says `exit 1` rather
           than "failed" on purpose: grep answers 1 for "no match", and only
           the reader — who can see the command — knows whether that is bad
           news or the answer they wanted. */
        {
            id: "t2",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.2,
            created_at: new Date().toISOString(),
            tool_name: "exec_command",
            tool_args: {
                cmd: "grep -rn \"a more human way to work\" drafts/",
                workdir: "/workspace/launch",
                comment: "Checking the old headline is gone everywhere, not just from v3.",
            },
            tool_call_id: "call_grep",
        },
        {
            id: "t2r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.21,
            created_at: new Date().toISOString(),
            tool_call_id: "call_grep",
            tool_result: { success: false, completed: true, exit_code: 1, stdout: "", stderr: "" },
        },
        {
            id: "t3",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.3,
            created_at: new Date().toISOString(),
            tool_name: "execute_python",
            tool_args: {
                code: "import pandas as pd\ntracker = pd.read_csv('tracker.csv')\ntracker[tracker.changed].shape[0]",
            },
            tool_call_id: "call_py",
        },
        {
            id: "t3r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.31,
            created_at: new Date().toISOString(),
            tool_call_id: "call_py",
            tool_result: {
                success: true,
                stdout: "",
                stderr: "",
                result: "1",
                execution_count: 4,
            },
        },
        {
            id: "t4",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.4,
            created_at: new Date().toISOString(),
            tool_name: "web_search",
            tool_args: { query: "Northfield pricing free tier removed", freshness: "week" },
            tool_call_id: "call_search",
        },
        {
            id: "t4r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.41,
            created_at: new Date().toISOString(),
            tool_call_id: "call_search",
            tool_result: {
                success: true,
                results: [
                    {
                        title: "Northfield retires its free plan",
                        url: "https://northfield.co/blog/pricing-update",
                        snippet:
                            "From 1 October new teams start on Starter at $12 a seat. Existing free workspaces keep their data and move across at renewal.",
                        source: "northfield.co",
                        publisher: "Northfield",
                        published_at: "3 days ago",
                    },
                    {
                        title: "Northfield drops free tier, raises Starter — what it means for small teams",
                        url: "https://www.saasweekly.com/northfield-free-tier",
                        snippet: "The second pricing change this year, and the first that removes an option rather than adding one.",
                        source: "saasweekly.com",
                        publisher: "SaaS Weekly",
                        published_at: "2 days ago",
                    },
                    {
                        title: "Pricing — Northfield",
                        url: "https://northfield.co/pricing",
                        snippet: "Starter $12 · Team $22 · Business $38, per seat, billed yearly.",
                        source: "northfield.co",
                    },
                ],
            },
        },
        {
            id: "t5",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.5,
            created_at: new Date().toISOString(),
            tool_name: "run_connector_operation",
            tool_args: {
                auth_config: "google_drive",
                operation: "GOOGLEDRIVE_FIND_FILE",
                arguments: { query: "design partners", mime_type: "application/vnd.google-apps.spreadsheet" },
            },
            tool_call_id: "call_drive",
        },
        {
            id: "t5r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.51,
            created_at: new Date().toISOString(),
            tool_call_id: "call_drive",
            tool_result: { result: [{ id: "1aBc", name: "Design partners (Maya)" }] },
        },
        /* A sign-in that was answered. The one still waiting is at the bottom
           of this conversation, because the link is the only control a paused
           sign-in has and an unanswered one is the state worth looking at. */
        {
            id: "t6",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.6,
            created_at: new Date().toISOString(),
            tool_name: "browser_sign_in",
            tool_args: {
                origin: "https://app.northfield.co",
                reason: "Their pricing page redirects to a login, and I need the plan table behind it.",
            },
            tool_call_id: "call_signin_done",
        },
        {
            id: "t6r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.61,
            created_at: new Date().toISOString(),
            tool_call_id: "call_signin_done",
            tool_result: { success: true, outcome: "signed_in", source: "person", origin: "https://app.northfield.co", saved: true },
        },
        /* The browser, all five moves, in the order a run actually makes them:
           open the page the sign-in unlocked, map it, click something, read
           what that revealed, photograph it. Every return shape here is the
           backend's own — `BrowserResult` is `{url, title, snapshot, output,
           truncated}` for the first four, and `browser_screenshot` answers
           with metadata alone or with a `ViewImageResponse` depending on
           whether the run's model can see. */
        {
            id: "b1",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.62,
            created_at: new Date().toISOString(),
            tool_name: "browser_open",
            tool_args: {
                url: "https://app.northfield.co/billing/plans",
                wait_for_url: "**/plans**",
                comment: "Their public pricing page is a marketing page; the real table is in the account.",
            },
            tool_call_id: "call_open",
        },
        {
            id: "b1r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.621,
            created_at: new Date().toISOString(),
            tool_call_id: "call_open",
            tool_result: {
                success: true,
                url: "https://app.northfield.co/billing/plans?from=nav",
                title: "Plans and billing · Northfield",
                snapshot: "@e1 link Overview\n@e4 link Plans\n@e12 button Compare plans\n@e18 table Plan comparison",
                truncated: false,
            },
        },
        {
            id: "b2",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.63,
            created_at: new Date().toISOString(),
            tool_name: "browser_snapshot",
            tool_args: { interactive_only: false, max_snapshot_tokens: 4000 },
            tool_call_id: "call_snap",
        },
        {
            id: "b2r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.631,
            created_at: new Date().toISOString(),
            tool_call_id: "call_snap",
            tool_result: {
                success: true,
                url: "https://app.northfield.co/billing/plans?from=nav",
                title: "Plans and billing · Northfield",
                snapshot: [
                    "@e1 link Overview",
                    "@e2 link Usage",
                    "@e4 link Plans",
                    "@e9 heading Choose a plan",
                    "@e12 button Compare plans",
                    "@e18 table Plan comparison",
                    "@e19 cell Starter",
                    "@e20 cell $12 per seat",
                    "@e21 cell Team",
                    "@e22 cell $22 per seat",
                    "…[snapshot truncated — narrow the page or scroll]…",
                ].join("\n"),
                truncated: true,
            },
        },
        {
            id: "b3",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.64,
            created_at: new Date().toISOString(),
            tool_name: "browser_act",
            tool_args: {
                action: "click",
                target: "@e12",
                wait_for_text: "Business",
                comment: "The third tier is behind the compare toggle.",
            },
            tool_call_id: "call_act",
        },
        {
            id: "b3r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.641,
            created_at: new Date().toISOString(),
            tool_call_id: "call_act",
            tool_result: {
                success: true,
                url: "https://app.northfield.co/billing/plans?from=nav&compare=1",
                title: "Compare plans · Northfield",
                snapshot: "@e18 table Plan comparison\n@e31 cell Business",
                truncated: false,
            },
        },
        {
            id: "b4",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.65,
            created_at: new Date().toISOString(),
            tool_name: "browser_read",
            tool_args: { what: "text", target: "@e18", max_output_tokens: 4000 },
            tool_call_id: "call_read",
        },
        {
            id: "b4r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.651,
            created_at: new Date().toISOString(),
            tool_call_id: "call_read",
            tool_result: {
                success: true,
                url: "https://app.northfield.co/billing/plans?from=nav&compare=1",
                title: "Compare plans · Northfield",
                output: [
                    "Plan\tSeat price\tBilled\tSeats included",
                    "Starter\t$12\tyearly\t1–10",
                    "Team\t$22\tyearly\t1–50",
                    "Business\t$38\tyearly\tunlimited",
                    "",
                    "Existing free workspaces move to Starter at their next renewal.",
                    "Annual billing only from 1 October; monthly is being retired with the free plan.",
                ].join("\n"),
                truncated: false,
            },
        },
        /* A read that failed, because the failure is the state easiest to get
           wrong: the browser is shed when the sandbox runs low on memory, and
           `classify_browser_failure` puts the explanation in `error` while
           `output` stays empty. */
        {
            id: "b5",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.66,
            created_at: new Date().toISOString(),
            tool_name: "browser_read",
            tool_args: { what: "console", comment: "The seat count cell rendered empty — checking whether a request failed." },
            tool_call_id: "call_console",
        },
        {
            id: "b5r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.661,
            created_at: new Date().toISOString(),
            tool_call_id: "call_console",
            tool_result: {
                success: false,
                error:
                    "The browser is not running. It is shed automatically when the sandbox runs low on memory, " +
                    "and closes itself after two minutes idle, so this is normal rather than a fault.",
            },
        },
        /* Two screenshots, because the tool answers in two shapes. A run whose
           model can see gets metadata only — the picture travels as binary
           tool content and never reaches `tool_result`, so the card has to say
           what was captured without pretending to show it. A run whose model
           cannot see gets a `ViewImageResponse` instead, whose `message` is the
           picture in words and whose `file_path` is the only address it
           carries. */
        {
            id: "b6",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.67,
            created_at: new Date().toISOString(),
            tool_name: "browser_screenshot",
            tool_args: {
                full_page: true,
                instructions: "Is the Business column actually filled in, or is it rendering empty?",
                comment: "Looking at the table rather than reading it, in case the cell is styled out.",
            },
            tool_call_id: "call_shot",
        },
        {
            id: "b6r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.671,
            created_at: new Date().toISOString(),
            tool_call_id: "call_shot",
            tool_result: {
                success: true,
                message: "Screenshot of https://app.northfield.co/billing/plans?from=nav&compare=1.",
                url: "https://app.northfield.co/billing/plans?from=nav&compare=1",
                title: "Compare plans · Northfield",
                media_type: "image/jpeg",
                size_bytes: 418_233,
                full_page: true,
            },
        },
        {
            id: "b7",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.68,
            created_at: new Date().toISOString(),
            tool_name: "browser_screenshot",
            tool_args: { annotate: true, instructions: "Which control switches the table to monthly?" },
            tool_call_id: "call_shot_seen",
        },
        {
            id: "b7r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.681,
            created_at: new Date().toISOString(),
            tool_call_id: "call_shot_seen",
            tool_result: {
                success: true,
                message:
                    "The table has three columns — Starter, Team, Business — each with a yearly seat price. " +
                    "There is no monthly toggle above the table; a line under it reads “Annual billing only " +
                    "from 1 October”, and the control that was there is greyed out at label 7.",
                file_path: "https://app.northfield.co/billing/plans?from=nav&compare=1",
                media_type: "image/png",
                source: "workspace",
                size_bytes: 271_904,
            },
        },
        /* And the tool that is the other half of that story. A screenshot's
           picture is made by the call and leaves as binary tool content, so
           there is nothing in the transcript to draw. `view_image` names a file
           that already exists, in one of two stores this app can read — so two
           of them, because the two stores are fetched in genuinely different
           ways: a pod file by signed URL, a workspace file as bytes off the
           sandbox. */
        {
            id: "b8",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.69,
            created_at: new Date().toISOString(),
            tool_name: "view_image",
            tool_args: {
                pod_file_path: "/launch/monday-hero.png",
                instructions: "Is the strapline inside the safe area, and is the logo legible at this size?",
            },
            tool_call_id: "call_look_pod",
        },
        {
            id: "b8r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.691,
            created_at: new Date().toISOString(),
            tool_call_id: "call_look_pod",
            tool_result: {
                success: true,
                /* The boilerplate a run that can see gets back. It says nothing
                   the card is not already showing, which is why the card drops
                   it rather than printing it as a description. */
                message: "Successfully read image /launch/monday-hero.png",
                file_path: "/launch/monday-hero.png",
                media_type: "image/png",
                source: "datastore",
                size_bytes: 842_118,
            },
        },
        {
            id: "b9",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.692,
            created_at: new Date().toISOString(),
            tool_name: "view_image",
            tool_args: {
                /* Relative, which is the ordinary spelling and the whole
                   difficulty: it resolves against the conversation's own
                   directory, not against `/workspace`. */
                workspace_file_path: "images/plans-table.png",
                instructions: "Read the three column headers and the price under each one.",
            },
            tool_call_id: "call_look_ws",
        },
        {
            id: "b9r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.693,
            created_at: new Date().toISOString(),
            tool_call_id: "call_look_ws",
            tool_result: {
                success: true,
                /* A run whose model cannot see: `message` is the picture in
                   words, written by the vision model the tool delegated to. */
                message:
                    "Three columns — Starter, Team, Business — each headed by a yearly seat price: £9, £19 and " +
                    "£39. The Business column is filled in; the cell that looked empty is the feature row " +
                    "beneath it, which has a tick rendered in white on white.",
                file_path: "images/plans-table.png",
                media_type: "image/png",
                source: "workspace",
                size_bytes: 271_904,
            },
        },
        {
            id: "t7",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 4.7,
            created_at: new Date().toISOString(),
            tool_name: "snooze",
            tool_args: { reason: "waiting for the nightly tracker refresh", seconds: 600, note_to_self: "Re-read the plan table and diff it." },
            tool_call_id: "call_snooze",
        },
        {
            id: "t7r",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 4.71,
            created_at: new Date().toISOString(),
            tool_call_id: "call_snooze",
            tool_result: {
                success: true,
                woke_because: "TIMER",
                slept_seconds: 600,
                note_to_self: "Re-read the plan table and diff it.",
                message: "Your time elapsed.",
            },
        },
        /* The two states of a pause, which is the whole reason this sample
           exists: one already answered, one still waiting. A card that can
           only be seen by having a live agent ask for something is a card
           nobody checks the layout of. */
        {
            id: "m5",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 5,
            created_at: new Date().toISOString(),
            tool_name: "request_approval",
            tool_call_id: "call_overwrite",
            tool_args: {
                tool_name: "pod_write_file",
                title: "Overwrite monday-launch-v3.md",
                reason: "v3 already exists and this replaces it outright. The old draft is not recoverable from here.",
                args: { path: "/launch/monday-launch-v3.md", bytes: 4120 },
            },
        },
        {
            id: "m6",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 6,
            created_at: new Date().toISOString(),
            tool_call_id: "call_overwrite",
            tool_result: { decision: "APPROVE_ONCE" },
        },
        {
            id: "m7",
            role: "assistant",
            kind: "TEXT",
            sequence: 7,
            created_at: new Date().toISOString(),
            text: "Written. The last thing is the quote — Jordan has not signed it off, and it is now the opening line.",
        },
        {
            id: "m8",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 8,
            created_at: new Date().toISOString(),
            tool_name: "ask_user",
            tool_call_id: "call_quote",
            tool_args: {
                title: "Which quote opens the post?",
                reason: "Both are signed off, and they pull in different directions. I would rather you picked.",
                /* Two questions, because one question is the case the card has
                   never had trouble with. An answered multi-question ask is
                   what the folded record is for. */
                questions: [
                    {
                        question: "Jordan's line is about hiring; Maya's is about the product. Which one opens?",
                        header: "Opening quote",
                        multi_select: false,
                        options: [
                            { label: "Jordan Kim", description: "\u201cWe stopped hiring for the gaps.\u201d Stronger, riskier." },
                            { label: "Maya Osei", description: "\u201cIt just does the work.\u201d Safer, less memorable." },
                        ],
                    },
                    {
                        question: "And where does the other one go?",
                        header: "The other quote",
                        multi_select: false,
                        options: [
                            { label: "Halfway down", description: "Against the paragraph about the rollout." },
                            { label: "Cut it", description: "One quote, and the post moves faster." },
                        ],
                    },
                ],
            },
        },
        {
            id: "m9",
            role: "assistant",
            kind: "TOOL_RETURN",
            sequence: 9,
            created_at: new Date().toISOString(),
            tool_call_id: "call_quote",
            tool_result: {
                decision: "APPROVE_ONCE",
                answers: { "Opening quote": "Jordan Kim", "The other quote": "Halfway down" },
            },
        },

        {
            id: "m11",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 11,
            created_at: new Date().toISOString(),
            tool_name: "display_resource",
            tool_args: { type: "FILE", path: "/me/videos/sample-clip.mp4" },
        },
        {
            id: "m11b",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 11.5,
            created_at: new Date().toISOString(),
            tool_name: "display_resource",
            tool_args: { type: "FILE", path: "/me/videos/thumbnail.png" },
        },
        /* A page, framed in the transcript. The card and the stage draw HTML
           two different ways — a sized preview here, the whole pane there —
           and neither was reachable in the sample. */
        {
            id: "m11c",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 11.6,
            created_at: new Date().toISOString(),
            tool_name: "display_resource",
            tool_args: { type: "FILE", path: "/launch-preview.html" },
        },
        {
            id: "m12",
            role: "assistant",
            kind: "TEXT",
            sequence: 12,
            created_at: new Date().toISOString(),
            text: "Cut and packaged. Six seconds, 640×360, sound on the bed.",
        },
        {
            id: "m10",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 10,
            created_at: new Date().toISOString(),
            tool_name: "request_approval",
            tool_call_id: "call_send",
            tool_args: {
                tool_name: "messages_send",
                title: "Email Jordan Kim to confirm the quote",
                reason:
                    "This goes outside the team. You told me to ask before anything leaves the building, and the draft " +
                    "quotes Jordan by name.",
                args: {
                    to: "jordan.kim@northfield.co",
                    subject: "Quick check on your quote for Monday",
                    body: "Hi Jordan — we would like to open the launch post with your line about hiring. Happy for us to use it?",
                },
            },
        },
        /* The pause the shelf above the composer is for: open, and more than
           one question deep. Asked one at a time, so what this fixture is
           really checking is that the second question exists at all without
           being on screen, and that answering the first folds it to a line.
         *
         *  It leaves the sample holding two open pauses at once, which a real
         *  run never does — a stopped run is stopped on exactly one thing. The
         *  shelf takes the last, so answering this one hands you the approval
         *  above it, which is the handover worth being able to look at. */
        {
            id: "m10b",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 10.2,
            created_at: new Date().toISOString(),
            tool_name: "ask_user",
            tool_call_id: "call_angle",
            tool_args: {
                /* No title, which is what a real `ask_user` usually arrives
                   with — the one above has one, so between them the card is
                   checked with the agent's own heading and without it. */
                reason: "Two decisions, and the second one only makes sense once the first is settled.",
                questions: [
                    {
                        question: "Which angle leads?",
                        header: "Angle",
                        multi_select: false,
                        options: [
                            { label: "What changed", description: "The product, and what is different on Monday." },
                            { label: "Why it took this long", description: "The honest version. Riskier, more read." },
                            { label: "Who it is for", description: "Straight at the design partners." },
                            { label: "What it cost", description: "Numbers up front. Dry, and hard to argue with." },
                        ],
                    },
                    {
                        question: "Who should see it before it goes out?",
                        header: "Reviewers",
                        multi_select: true,
                        options: [
                            { label: "Jordan Kim", description: "Quoted in it." },
                            { label: "Maya Osei", description: "Signed off the product claims already." },
                            { label: "Legal", description: "Adds about a day." },
                        ],
                    },
                ],
            },
        },
        /* The pause that stopped the run, unanswered, and last because that is
           what it is: the reason there is nothing after it. `browser_sign_in`
           is answered from the card now — it opens the teammate's own browser
           on the site — so this is the state worth looking at. */
        {
            id: "m13",
            role: "assistant",
            kind: "TOOL_CALL",
            sequence: 13,
            created_at: new Date().toISOString(),
            tool_name: "browser_sign_in",
            tool_call_id: "call_signin_open",
            tool_args: {
                origin: "https://partners.northfield.co",
                reason: "The design-partner portal wants a login, and the renewal dates are only in there.",
            },
        },
    ],
};

/* A history longer than one page. Pagination is the last thing in this
   transcript that could only be seen by having a real conversation with more
   than a hundred messages in it, which is to say it was never looked at: the
   cursor was dropped, the "Earlier" control was never passed, and nothing said
   the thread had a top it had not reached. These are the earlier turns, and
   the pane hands them over a page at a time. */
const EARLIER: Message[] = Array.from({ length: 12 }, (_, index) => {
    const round = index + 1;
    const at = new Date(Date.now() - (13 - round) * 11 * 60_000).toISOString();
    return [
        {
            id: "old-u" + round,
            role: "user",
            kind: "TEXT",
            sequence: round * 2 - 1,
            created_at: at,
            text: EARLIER_ASKS[index % EARLIER_ASKS.length],
        },
        {
            id: "old-a" + round,
            role: "assistant",
            kind: "TEXT",
            sequence: round * 2,
            created_at: at,
            text: EARLIER_REPLIES[index % EARLIER_REPLIES.length],
        },
    ];
}).flat();

/** Enough of the conversation to reach back through, newest last. */
const FULL_HISTORY: Message[] = [
    ...EARLIER,
    ...CONVERSATION.messages.map((message) => ({ ...message, sequence: (message.sequence ?? 0) + 100 })),
];

const PROFILE: Profile = {
    podId: "r1",
    name: "Marketing",
    iconUrl: "📣",
    headline: "Runs the launch: assets, copy, and a competitor watch nobody has to remember",
    joined: "2026-04-12T09:00:00Z",
    about:
        "You are the marketing teammate for Lemma. You keep the launch assets current, " +
        "watch five named competitors, and tell the team when something moves. Ask before " +
        "publishing anything outward.",
    /* The twelve a pod's own responder actually runs with, built through the
       same table the live source reads — not a hand-picked few. `pod_default`
       reports `toolsets: []` and the set is applied at run time, so a
       hand-written list here is a copy of what the live source appears to
       produce rather than of what it means, and a sample that agrees with a
       bug is a sample that cannot show the bug. */
    skills: capabilityList(POD_DEFAULT_TOOLSETS).map((capability) => ({
        id: capability.code,
        label: capability.word,
        blurb: capability.says,
    })),
    /* Still carried, still not drawn. `allowed_actions` are the viewer's
       permissions on an agent row and they were being printed under a heading
       saying Skills; the Agents detail lists them under "You may", which is
       where a permission belongs. */
    permits: ["files.write", "tables.write", "messages.send"],
    /* Filled in by `getProfile` from `SCHEDULES` below, which is the same
       payload the Standing work section reads. Two hand-written commitments
       sat here before and drifted from it the moment a schedule was paused. */
    commitments: [],
    projects: [
        { id: "a1", name: "Launch tracker", description: "Where the five competitors live", status: "running", tabId: "app:launch-tracker" },
    ],
    counts: { tables: 2, functions: 3, workflows: 1 },
};

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/* ── standing work ───────────────────────────────────────────────────
   Wire shapes, not `StandingJob`s, and deliberately so: the sample runs
   through `readSchedules` exactly as the live source does, which is the only
   way a guard written for a payload nobody here can send gets exercised at
   all. One row is malformed on purpose for that reason.

   Every state the section has to draw is present once — healthy, filtered,
   never fired, paused by a person, stopped by the failure breaker, internal
   (which must not appear), and unreadable. A list that only ever renders the
   happy row cannot be judged. */

const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const MINUTES = 60_000;
const HOURS = 60 * MINUTES;
const DAYS = 24 * HOURS;

let SCHEDULES: Record<string, unknown>[] = [
    {
        id: "s1",
        pod_id: "r1",
        name: "daily_tracker_refresh",
        schedule_type: "TIME",
        agent_name: "researcher",
        agent_id: "ag-1",
        config: { cron: "0 9 * * 1-5", timezone: "Europe/Berlin" },
        instruction: "Re-read the five competitors and write what changed into the tracker.",
        filter_instruction: null,
        filter_output_schema: null,
        account_id: null,
        connector_trigger_id: null,
        visibility: "POD",
        is_active: true,
        is_internal: false,
        paused_by_failures: false,
        last_fired_at: ago(6 * HOURS),
        last_fire_status: "TRIGGERED",
        last_error: null,
        consecutive_failures: 0,
        created_at: "2026-04-20T09:00:00Z",
        allowed_actions: ["schedule.read", "schedule.update", "schedule.delete"],
    },
    {
        id: "s2",
        pod_id: "r1",
        name: "press_inbox_watch",
        schedule_type: "WEBHOOK",
        workflow_name: "press_triage",
        workflow_id: "wf-1",
        config: { source: "slack" },
        instruction: null,
        filter_instruction: "Only when the message names one of the five competitors.",
        filter_output_schema: { type: "object", properties: { matters: { type: "boolean" } } },
        account_id: "acct-1",
        connector_trigger_id: "slack_message_posted",
        visibility: "POD",
        is_active: true,
        is_internal: false,
        paused_by_failures: false,
        last_fired_at: ago(40 * MINUTES),
        last_fire_status: "FILTERED",
        last_error: null,
        consecutive_failures: 0,
        created_at: "2026-06-02T09:00:00Z",
        allowed_actions: ["schedule.read", "schedule.update"],
    },
    {
        id: "s3",
        pod_id: "r1",
        name: "weekly_launch_digest",
        schedule_type: "TIME",
        workflow_name: "launch_digest",
        workflow_id: "wf-2",
        config: { cron: "0 8 * * 1" },
        instruction: null,
        filter_instruction: null,
        filter_output_schema: null,
        account_id: null,
        connector_trigger_id: null,
        visibility: "POD",
        is_active: false,
        is_internal: false,
        /* The breaker stopped it, and the server is the one that says so — the
           threshold is a deployment setting no client can see. */
        paused_by_failures: true,
        last_fired_at: ago(2 * DAYS),
        last_fire_status: "ERROR",
        last_error: "Workflow 'launch_digest' has no step that can accept a schedule event.",
        consecutive_failures: 5,
        created_at: "2026-05-11T09:00:00Z",
        allowed_actions: ["schedule.read", "schedule.update", "schedule.delete"],
    },
    {
        id: "s4",
        pod_id: "r1",
        name: "new_lead_greeting",
        schedule_type: "DATASTORE",
        agent_name: "POD_DEFAULT",
        agent_id: "ag-0",
        config: { table_name: "contacts", operations: ["INSERT"], when: { status: { eq: "New" } } },
        instruction: "Write the first reply and leave it in drafts.",
        filter_instruction: null,
        filter_output_schema: null,
        account_id: null,
        connector_trigger_id: null,
        visibility: "POD",
        is_active: true,
        is_internal: false,
        paused_by_failures: false,
        last_fired_at: null,
        last_fire_status: null,
        last_error: null,
        consecutive_failures: 0,
        created_at: ago(3 * DAYS),
        allowed_actions: ["schedule.read", "schedule.update", "schedule.delete"],
    },
    {
        id: "s5",
        pod_id: "r1",
        name: "quarterly_archive",
        schedule_type: "TIME",
        agent_name: "researcher",
        agent_id: "ag-1",
        config: { cron: "0 3 1 1,4,7,10 *" },
        instruction: "Move last quarter's tracker rows into the archive table.",
        visibility: "POD",
        is_active: false,
        is_internal: false,
        paused_by_failures: false,
        last_fired_at: ago(46 * DAYS),
        last_fire_status: "TRIGGERED",
        last_error: null,
        consecutive_failures: 0,
        created_at: "2026-04-22T09:00:00Z",
        allowed_actions: ["schedule.read"],
    },
    {
        /* Workflow execution's own timer. The API already leaves these out of
           the list; the reader drops them again so a deployment that stops
           filtering does not put a thirty-minute wait on somebody's profile. */
        id: "s6",
        pod_id: "r1",
        name: null,
        schedule_type: "TIME",
        workflow_name: "press_triage",
        config: { scheduled_at: ago(-30 * MINUTES) },
        is_active: true,
        is_internal: true,
        paused_by_failures: false,
        consecutive_failures: 0,
        created_at: ago(30 * MINUTES),
        allowed_actions: [],
    },
    {
        /* Not a schedule shape at all. It draws as a row that says so. */
        schedule_type: "CONJURED",
        config: null,
    },
];

const SCHEDULE_RUNS: Record<string, Record<string, unknown>[]> = {
    s1: [
        {
            id: "r-1a", schedule_id: "s1", status: "COMPLETED", attempts: 1, target_kind: "AGENT",
            target_run_id: "ar-91", source_occurred_at: ago(6 * HOURS), started_at: ago(6 * HOURS),
            completed_at: ago(6 * HOURS - 90_000), created_at: ago(6 * HOURS), payload: {}, metadata: {}, llm_output: {},
        },
        {
            id: "r-1b", schedule_id: "s1", status: "COMPLETED", attempts: 1, target_kind: "AGENT",
            target_run_id: "ar-88", source_occurred_at: ago(1 * DAYS), created_at: ago(1 * DAYS),
            payload: {}, metadata: {}, llm_output: {},
        },
    ],
    s2: [
        {
            id: "r-2a", schedule_id: "s2", status: "FILTERED", attempts: 1, target_kind: "WORKFLOW",
            target_run_id: null, source_occurred_at: ago(40 * MINUTES), created_at: ago(40 * MINUTES),
            payload: { text: "Reminder: standup moved to 10." }, metadata: {}, llm_output: { matters: false },
        },
        {
            id: "r-2b", schedule_id: "s2", status: "COMPLETED", attempts: 1, target_kind: "WORKFLOW",
            target_run_id: "wr-17", source_occurred_at: ago(5 * HOURS), created_at: ago(5 * HOURS),
            payload: {}, metadata: {}, llm_output: { matters: true },
        },
    ],
    s3: [
        {
            /* Dispatched fine and the workflow then failed — which arrives as
               TARGET_FAILED, because the response's `status` is already the
               effective one. Retryable. */
            id: "r-3a", schedule_id: "s3", status: "TARGET_FAILED", attempts: 1, target_kind: "WORKFLOW",
            target_run_id: "wr-22", error_type: "TargetFailed", error_code: "NO_MATCHING_START",
            source_occurred_at: ago(2 * DAYS), started_at: ago(2 * DAYS), completed_at: ago(2 * DAYS),
            created_at: ago(2 * DAYS), payload: {}, metadata: {}, llm_output: {},
        },
        {
            id: "r-3b", schedule_id: "s3", status: "FAILED", attempts: 3, target_kind: "WORKFLOW",
            target_run_id: null, error_type: "DispatchError", error_code: null,
            source_occurred_at: ago(9 * DAYS), created_at: ago(9 * DAYS), payload: {}, metadata: {}, llm_output: {},
        },
        {
            id: "r-3c", schedule_id: "s3", status: "COMPLETED", attempts: 1, target_kind: "WORKFLOW",
            target_run_id: "wr-11", redrive_of_run_id: "r-3d",
            source_occurred_at: ago(16 * DAYS), created_at: ago(16 * DAYS), payload: {}, metadata: {}, llm_output: {},
        },
    ],
    s4: [],
    s5: [],
};

/* Channels, as state: the sample can actually connect and disconnect. A
   connect flow you can look at but not walk is a screenshot, and the states
   worth judging here are the ones between the click and the address. */
const SAMPLE_HANDLE: Record<string, string> = {
    TELEGRAM: "@marketing_acme_bot",
    WHATSAPP: "+1 555-629-5168",
    RESEND: "marketing@ops.example.invalid",
    SLACK: "Marketing",
    TEAMS: "Marketing",
};

let SURFACES: Surface[] = [
    { id: "s3", platform: "RESEND", name: "email", mine: true, agentName: "Marketing", handle: "marketing@ops.example.invalid", email: "marketing@ops.example.invalid", active: true },

    { id: "s4", platform: "TELEGRAM", name: "roaster", mine: false, agentName: "Roaster", handle: "@roaster_bot", active: true },
    /* Bound to a subagent, so deleting that agent has something to name. The
       backend tears this down with the agent — see `surfacesLost`. */
    { id: "s5", platform: "TELEGRAM", name: "researcher", mine: false, agentName: "Researcher", handle: "@acme_research_bot", active: true },
];

let guidedStartedAt = 0;
let accountStartedAt = 0;
let accountPending = "";

/* The authorisation lands on the backend, not here, so in the sample it lands
   on a timer: anything that reads accounts settles a pending one first. Both
   the reach sheet (which polls) and the connectors page (which just re-reads)
   then see the same account appear, which is how the real thing behaves. */
function settleAccount(): void {
    if (!accountPending || Date.now() - accountStartedAt <= 5_000) return;
    const id = "acc-" + accountPending;
    if (!ACCOUNTS.some((raw) => (raw as { id?: string }).id === id)) {
        ACCOUNTS = [...ACCOUNTS, {
            id, connector_id: accountPending, display_name: "Acme workspace",
            status: "CONNECTED", install_state: "READY", created_at: new Date().toISOString(),
        }];
    }
    accountPending = "";
}

/* Shaped like the wire, so the sample exercises the real readers — including
   the two fields that were being read wrong (`title`, and a status of
   CONNECTED rather than ACTIVE). */
const CONNECTORS: unknown[] = [
    { id: "slack", title: "Slack", description: "Messaging, channels and notifications.", icon: "/connector-logos/slack.svg", is_active: true },
    /* With its kind, as the wire has it: a bot token over `http`, with no
       address to supply — so the sample walks the credential form. */
    { id: "telegram", title: "Telegram", description: "Bots and direct messages.", icon: "/connector-logos/telegram.svg", is_active: true,
        kinds: [{ kind: "http", auth_scheme: "API_KEY", system_default_available: true,
            config_schema: { type: "object", properties: {} },
            credential_schema: { type: "object", required: ["bot_token"], properties: {
                bot_token: { type: "string", title: "Bot token", description: "From @BotFather." } } } }] },
    { id: "whatsapp", title: "WhatsApp Business", description: "The number your customers already use.", icon: "/connector-logos/whatsapp.svg", is_active: true },
    { id: "teams", title: "Microsoft Teams", description: "Chat and channels in Microsoft 365.", icon: "/connector-logos/teams.svg", is_active: true },
    { id: "github", title: "GitHub", description: "Repositories, issues and pull requests.", icon: "", is_active: true },
    { id: "notion", title: "Notion", description: "Pages and databases.", icon: "", is_active: true },
    { id: "gmail", title: "Gmail", description: "Read and send as a mailbox.", icon: "", is_active: true },
];

let ACCOUNTS: unknown[] = [
    /* The hard case, present on purpose: an org that already holds the
       account, so nothing NEW ever appears and a flow watching for a new row
       waits forever. */
    /* No display_name, exactly as Slack returns it — so the sample shows the
       fallback rather than a raw `U077S2UCG4S` where a name belongs. */
    { id: "acc-slack", connector_id: "slack", provider_account_id: "U077S2UCG4S", status: "CONNECTED", install_state: "READY", created_at: "2026-08-14T10:00:00Z" },
    { id: "acc-gmail", connector_id: "gmail", display_name: "deepak@acme.com", status: "CONNECTED", install_state: "READY", is_default: true, created_at: "2026-08-01T10:00:00Z" },
    /* The three unfinished states, because each needs a different sentence
       and a boolean would have hidden all of them. */
    { id: "acc-github", connector_id: "github", display_name: "acme-eng", status: "CONNECTED", install_state: "INSTALL_REQUIRED", created_at: "2026-08-20T10:00:00Z" },
    { id: "acc-notion", connector_id: "notion", display_name: "Acme workspace", status: "REAUTH_REQUIRED", install_state: "READY", created_at: "2026-07-02T10:00:00Z" },
];

/* Shaped exactly like `podSurfaces.available()`, so the sample exercises the
   real reading in `connectable.ts` rather than a convenient parallel one. */
const CONNECTABLE: unknown[] = [
    {
        platform: "TELEGRAM", connector_id: "telegram", title: "Telegram", connector_available: true, managed_setup_available: true,
        supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: { available: true },
        description: "A bot people message directly.",
    },
    {
        platform: "WHATSAPP", connector_id: "whatsapp", title: "WhatsApp", connector_available: true,
        supported_credential_modes: ["CUSTOM", "SYSTEM"],
        /* The shared number is claimable once per organization, and this org
           already spent it. Said before the click, not after a failed save. */
        system_claim: { available: false, claimed_by_pod_id: "r2", claimed_by_surface_name: "whatsapp" },
        description: "Your teammate on the number people already use.",
    },
    {
        platform: "RESEND", connector_id: "resend", title: "Email", connector_available: false, email_domain: "ops.example.invalid",
        supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: { available: true },
        description: "An address of its own. Write to it like a colleague.",
    },
    {
        platform: "SLACK", connector_id: "slack", title: "Slack", connector_available: true,
        supported_credential_modes: ["CUSTOM"], connect: { system_oauth_available: true },
        description: "In the channels your team already works in.",
    },
    {
        platform: "TEAMS", connector_id: "teams", title: "Microsoft Teams", connector_available: true,
        supported_credential_modes: ["CUSTOM"], connect: { system_oauth_available: false },
        description: "For teams that live in Microsoft 365.",
    },
];

/* ── what the sample teammates run on ──────────────────────────────────
   Four rows and two machines, because that is the shape that can actually
   be judged: a built-in, a bought key, a coding agent that works, one that
   is signed out, and a laptop that is asleep. A single happy row would
   have made the ledger look like a list of one. */
let RUNTIMES: unknown[] = [
    {
        id: "system:lemma", name: "Lemma", kind: "MODEL_PROVIDER", scope: "SYSTEM", status: "ACTIVE",
        default_model_name: "anthropic/models/claude-sonnet-5",
        model_catalog: [
            { name: "anthropic/models/claude-sonnet-5", display_name: "Claude Sonnet 5" },
            { name: "anthropic/models/claude-opus-5", display_name: "Claude Opus 5" },
            { name: "openai/models/gpt-5", display_name: "GPT-5" },
        ],
    },
    {
        id: "org:openrouter", name: "OpenRouter", kind: "MODEL_PROVIDER", scope: "ORGANIZATION", status: "ACTIVE",
        default_model_name: "deepseek/deepseek-chat",
        model_catalog: [
            { name: "deepseek/deepseek-chat" },
            { name: "meta-llama/llama-4-70b" },
        ],
    },
    {
        id: "me:claude-code", name: "Claude Code", kind: "HARNESS", scope: "PERSONAL", status: "ACTIVE",
        harness_id: "h-claude", availability_status: "READY", default_model_name: "sonnet",
        metadata: { harness_key: "claude-code" },
        model_catalog: [{ name: "sonnet", display_name: "Sonnet" }, { name: "opus", display_name: "Opus" }],
    },
    {
        /* Added, and its computer is asleep — the state a picker has to be
           able to say out loud, because it is not broken now and will be
           the moment somebody asks. */
        id: "me:opencode", name: "OpenCode", kind: "HARNESS", scope: "PERSONAL", status: "ACTIVE",
        harness_id: "h-opencode", availability_status: "OFFLINE", default_model_name: "qwen3-coder",
        metadata: { harness_key: "opencode" },
        model_catalog: [{ name: "qwen3-coder", display_name: "Qwen3 Coder" }],
    },
    {
        id: "org:old-key", name: "Last year's key", kind: "MODEL_PROVIDER", scope: "ORGANIZATION",
        status: "DISABLED", default_model_name: "gpt-4o",
    },
];

const COMPUTERS: unknown[] = [
    {
        id: "c-mac", display_name: "Deepak's MacBook", status: "ONLINE", host_release: "0.7.2",
        created_at: "2026-09-01T09:00:00Z", last_seen_at: "2026-09-15T08:58:00Z",
    },
    {
        id: "c-studio", display_name: "Studio", status: "OFFLINE", host_release: "0.7.1",
        created_at: "2026-08-12T09:00:00Z", last_seen_at: "2026-09-14T19:12:00Z",
    },
];

const AGENTS_ON: Record<string, unknown[]> = {
    "c-mac": [
        {
            id: "h-claude", harness_key: "claude-code", display_name: "Claude Code", health: "READY",
            upstream_version: "2.1.233",
            config_options: [
                {
                    id: "model", category: "model", current_value: "sonnet",
                    options: [{ value: "sonnet", name: "Sonnet" }, { value: "opus", name: "Opus" }],
                },
                /* The host has already taken the modes Lemma refuses out of
                   this list, and marked it `policy`. */
                {
                    id: "mode", category: "mode", name: "Mode", current_value: "default", metadata: { policy: true },
                    options: [{ value: "default", name: "Ask before edits" }, { value: "plan", name: "Plan" }],
                },
            ],
        },
        {
            /* Installed and signed out — the state that reads as broken
               until the row says which of the two it is. */
            id: "h-codex", harness_key: "codex", display_name: "codex", health: "AUTH_REQUIRED",
            upstream_version: "0.41.0",
            config_options: [{ category: "model", options: [{ value: "gpt-5-codex", name: "GPT-5 Codex" }] }],
        },
        {
            /* Ready, and nobody has added it — the state the Add button
               exists for, and the one a ledger of only-added agents could
               not be judged in. */
            id: "h-cursor", harness_key: "cursor", display_name: "Cursor", health: "READY",
            upstream_version: "1.7.44",
            config_options: [{
                category: "model",
                options: [{ value: "auto", name: "Auto" }, { value: "claude-4.5-sonnet", name: "Claude 4.5 Sonnet" }],
            }],
        },
    ],
    "c-studio": [
        {
            id: "h-opencode", harness_key: "opencode", display_name: "OpenCode", health: "READY",
            upstream_version: "1.4.0",
            config_options: [
                { id: "model", category: "model", current_value: "qwen3-coder", options: [{ value: "qwen3-coder", name: "Qwen3 Coder" }] },
                {
                    id: "effort", category: "thought_level", name: "Effort", current_value: "high",
                    options: [{ value: "low", name: "Low" }, { value: "high", name: "High" }, { value: "max", name: "Max" }],
                },
            ],
        },
    ],
};

/* The agents behind a teammate, shaped like the wire so the sample exercises
   the real readers rather than a second set of shapes that cannot be wrong.
   One of each state the view has to draw: the agent you talk to (which reports
   every permission and refuses two of them anyway), one that can be edited and
   deleted, one that takes typed arguments instead of a conversation, one that
   is read-only from here, and one that arrived broken. */
let AGENTS: Record<string, unknown>[] = [
    {
        id: "a-default", name: "pod_default", kind: "POD_DEFAULT",
        description: "Answers here, and hands work to the rest.",
        instruction: [
            "You are Marketing, the teammate for Acme's marketing pod.",
            "",
            "You draft campaign copy, keep the launch calendar, and answer questions about",
            "what has shipped. You never send anything to a customer without asking Priya",
            "first — quotes and pricing are hers to approve.",
            "",
            "When a task needs research, hand it to `researcher`. When an invoice arrives,",
            "hand it to `invoice-filer` with the file path.",
        ].join("\n"),
        visibility: "POD",
        toolsets: ["WORKSPACE_CLI", "WEB_SEARCH", "POD", "SUBAGENTS", "MEMORY", "MESSAGING", "TODO"],
        /* Every one of the four, which is the point: the pod's own assistant
           is the row that reports the most permission and accepts the least. */
        allowed_actions: ["agent.read", "agent.execute", "agent.update", "agent.delete"],
        has_pinned_runtime: true, takes_input: false,
        agent_runtime: { profile_id: "me:claude-code", model_name: "opus" },
        permissions: { grants: [
            { resource_type: "DATASTORE_TABLE", resource_name: "campaigns", permission_ids: ["record.read", "record.write"] },
            { resource_type: "FOLDER", resource_name: "/shared/brand", permission_ids: ["document.read"] },
        ] },
        updated_at: "2026-09-16T11:02:00Z",
    },
    {
        id: "a-researcher", name: "researcher",
        description: "Reads the open web and writes up what it found. Cites everything, and says when a source disagrees with another.",
        instruction: [
            "You research questions for the marketing pod.",
            "",
            "Always cite. Prefer a primary source to a summary of one. If two sources",
            "disagree, say so in the write-up rather than picking one.",
            "",
            "Write findings to /shared/research as markdown, one file per question.",
        ].join("\n"),
        visibility: "POD",
        toolsets: ["WEB_SEARCH", "BROWSER", "POD", "MEMORY"],
        allowed_actions: ["agent.read", "agent.execute", "agent.update", "agent.delete"],
        has_pinned_runtime: false, takes_input: false,
        permissions: { grants: [
            { resource_type: "FOLDER", resource_name: "/shared/research", permission_ids: ["document.read", "document.write"] },
        ] },
        updated_at: "2026-09-12T08:41:00Z",
    },
    {
        /* Typed inputs: this one is *called*, not talked to, and the list has
           to say so — "open its conversation" is the wrong offer for it. */
        id: "a-filer", name: "invoice-filer",
        description: "Reads an invoice and files it against the right supplier.",
        instruction: "Given an invoice file, extract the supplier, total and due date, and write a row to `invoices`. If the supplier is unknown, stop and ask.",
        visibility: "RESTRICTED",
        toolsets: ["POD", "USER_INTERACTION"],
        allowed_actions: ["agent.read", "agent.update"],
        has_pinned_runtime: false, takes_input: true,
        input_schema: {
            type: "object",
            properties: { path: { type: "string" }, supplier_hint: { type: "string" } },
            required: ["path"],
        },
        output_schema: { type: "object", properties: { invoice_id: { type: "string" }, total: { type: "number" } } },
        updated_at: "2026-09-09T15:20:00Z",
    },
    {
        /* Read-only from here: someone else's agent, granted `agent.read`
           alone. The row still opens; it offers nothing. */
        id: "a-digest", name: "nightly-digest",
        description: "Sends the 7am summary.",
        instruction: "Each morning, summarise yesterday's campaign numbers in under 120 words and message the pod.",
        visibility: "POD",
        toolsets: ["MESSAGING", "POD"],
        allowed_actions: ["agent.read"],
        has_pinned_runtime: true, takes_input: false,
        agent_runtime: { profile_id: "org:openai-key" },
        updated_at: "2026-08-30T06:00:00Z",
    },
    /* No name. There is nothing to open, edit or delete it by, and a pod
       showing four of its five agents with no sign of the fifth is worse than
       one showing a row that admits it. */
    { id: "a-broken", description: "Something went wrong upstream.", allowed_actions: ["agent.read"] },
];

/** What each sample pod has been told to run on. Most have been told
 *  nothing, which is the common case and the one worth drawing. */
const POD_RUNTIME: Record<string, { profile_id: string; model_name?: string | null }> = {
    marketing: { profile_id: "me:claude-code", model_name: "opus" },
};

/** And who each has been told may let themselves in. Most have been told
    nothing either, which reads as the shut door — so one pod stands open, to
    make the difference between the rungs visible on one screen. */
const POD_JOIN: Record<string, JoinPolicy> = {
    marketing: "org",
    /* A teammate elsewhere in this organization with its door open, which is
       not in `PODS` because you are not in it yet. It exists so the admitted
       path can actually be walked here: knock on `/t/open-teammate` and the
       sample source lets you in on the spot and grows the rail, the way an
       `ORG_MEMBERS` pod does on the real thing. Without it every ask in this
       source answers `pending` and half the screen is undrawn. */
    "open-teammate": "anyone",
};

/** Who is waiting at a teammate's door.
 *
 *  Two of them, on the one pod this source lets you look at, because an empty
 *  queue is the state that needs no design work and a queue of one hides
 *  whether the rows stack. The second has no name on it on purpose: the
 *  platform fills `user_name` from a profile a brand-new account has not
 *  written yet, and a brand-new account is exactly who knocks on a door.
 */
const KNOCKING: JoinRequest[] = [
    {
        id: "jr-1",
        podId: "marketing",
        standing: "pending",
        name: "Tomas Ruiz",
        email: "tomas.ruiz@acme.test",
        askedAt: ago(28 * 60 * 60_000),
    },
    {
        id: "jr-2",
        podId: "marketing",
        standing: "pending",
        name: "",
        email: "n.okafor@acme.test",
        askedAt: ago(3 * 60 * 60_000),
    },
];

/** What this browser has asked for, so a reload says "waiting" rather than
 *  offering the ask a second time. Sample mode has no server to remember it,
 *  and the cache is this source's whole state — the same trick the approval
 *  card and the conversation title use. */
const ASKED: Record<string, JoinRequest> = {};

/** And the organization's own door, which is the one the pod's is measured
    against: a pod cannot be opened wider than what is around it. Set to the
    domain rule, so the field that rule needs is on screen by default. */
const ORG_JOIN: Record<string, OrgJoin> = {
    acme: { policy: "domain", domain: "acme.com" },
};

/** A document with enough in it to judge type by: headings at three levels,
 *  prose that runs past one line, a list, a table, code and a quote. */
const SAMPLE_DOCUMENT = [
    "# Launch readiness — week of 14 September",
    "",
    "**As of:** 2026-09-14 · **Scope:** the Monday post and the assets that go with it · **Status:** draft, one approval outstanding",
    "",
    "## The short answer",
    "",
    "The post is written and the assets are cut. What is left is a person: Jordan has not",
    "signed off the quote that now opens the piece, and the design-partner list is Maya's to",
    "confirm. Neither is blocking the build, and both block publication.",
    "",
    "Everything else moved this week. The headline lost the line about *a more human way to",
    "work*, which was doing no work, and the third paragraph is gone entirely.",
    "",
    "## What changed in v3",
    "",
    "- Jordan Kim's quote now opens the piece, ahead of the product line",
    "- The competitor sweep found one move worth naming — Northfield dropped its free tier",
    "- Cut the third paragraph; it repeated the opening in weaker words",
    "- Swapped the hero image for the cut from the launch video",
    "",
    "> The customer story is stronger than the product story. Lead with it and let the",
    "> features arrive as evidence.",
    "",
    "## Where each asset stands",
    "",
    "| Asset | State | Owner | Blocked on |",
    "| --- | --- | --- | --- |",
    "| Monday post | Draft v3 | Marketing | Jordan's quote |",
    "| Hero image | Final | Marketing | — |",
    "| Launch video | Final, 1:35 | Video Man | — |",
    "| Design partners | Outline | Maya | Confirmation |",
    "| Pricing page | Not started | Marketing | Post ships first |",
    "",
    "## How to read the draft",
    "",
    "The working copy is at `/launch/monday-launch-v3.md` and the previous version is kept",
    "beside it. To see only what moved between them:",
    "",
    "```bash",
    "diff /launch/monday-launch-v2.md /launch/monday-launch-v3.md",
    "```",
    "",
    "### Open questions",
    "",
    "Two, and both need a person rather than another draft. Whether the quote can run at all",
    "without Jordan confirming it, and whether the design-partner list names five or names",
    "none. The second changes the closing paragraph either way.",
].join("\n");

/** An HTML file, which the sample had none of — so the one format with two
 *  render paths and a theme bridge across an iframe boundary was the one format
 *  nobody could look at without a live pod and an agent that happened to write
 *  a page. It reads the `--lemma-widget-*` tokens on purpose: switching theme
 *  should carry into the frame, and that is only checkable against content that
 *  asked for the tokens. */
const SAMPLE_PAGE = [
    "<!doctype html>",
    "<meta charset=\"utf-8\">",
    "<style>",
    "  body { margin:0; padding:32px 36px 40px; font-family:var(--lemma-widget-font, system-ui, sans-serif);",
    "         background:var(--lemma-widget-surface, #fff); color:var(--lemma-widget-text, #111); }",
    "  h1 { margin:0 0 6px; font-size:26px; font-weight:500; letter-spacing:-.01em; }",
    "  p.stand { margin:0 0 28px; color:var(--lemma-widget-muted, #555); font-size:15px; line-height:1.6; max-width:62ch; }",
    "  .figures { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:28px; }",
    "  .fig { border:1px solid var(--lemma-widget-border, #e4e4e4); border-radius:var(--lemma-widget-radius-md, 10px);",
    "         padding:14px 16px; background:var(--lemma-widget-subtle, #fafafa); }",
    "  .fig b { display:block; font-size:24px; font-weight:500; color:var(--lemma-widget-accent, #6b4fe0); }",
    "  .fig span { font-size:12px; color:var(--lemma-widget-faint, #888); }",
    "  table { border-collapse:collapse; width:100%; font-size:14px; }",
    "  th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--lemma-widget-border, #e4e4e4); }",
    "  th { font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--lemma-widget-faint, #888); font-weight:400; }",
    "</style>",
    "<h1>Monday launch &mdash; preview</h1>",
    "<p class=\"stand\">Built from the working draft. Everything below is generated, so this page is",
    "the thing to check the layout against rather than a record of anything.</p>",
    "<div class=\"figures\">",
    "  <div class=\"fig\"><b>1,840</b><span>words in the draft</span></div>",
    "  <div class=\"fig\"><b>4</b><span>figures still open</span></div>",
    "  <div class=\"fig\"><b>2</b><span>quotes awaiting sign-off</span></div>",
    "</div>",
    "<table>",
    "  <tr><th>Section</th><th>State</th><th>Owner</th></tr>",
    "  <tr><td>Opening</td><td>Drafted</td><td>Marketing</td></tr>",
    "  <tr><td>What changed</td><td>Drafted</td><td>Marketing</td></tr>",
    "  <tr><td>Design partners</td><td>Outline</td><td>Maya</td></tr>",
    "  <tr><td>Pricing</td><td>Not started</td><td>&mdash;</td></tr>",
    "</table>",
].join("\n");

/* ── skills ──────────────────────────────────────────────────────────
   A hand, not a happy row. Every state the deck has to draw is here once:
   two that load, one whose frontmatter has no `description`, one renamed in
   its frontmatter without its folder being renamed, and one whose SKILL.md
   cannot be read at all. The last three are the point — the loader skips all
   of them silently and the teammate simply never sees those skills, so a deck
   that only ever renders the healthy card cannot be judged and cannot be
   trusted to survive a real pod.

   Written as whole files rather than as parsed objects, so the sample runs
   through `readFrontmatter` exactly as the live source does. */
const SAMPLE_SKILLS: { folder: string; updated: string; md: string | null }[] = [
    {
        /* A real one. Every other description here is a sentence, and a card
           that only ever meets a sentence cannot show that a paragraph breaks
           it — which is exactly what happened: on a real pod the description
           pushed the footer out through the bottom of the card, and the sample
           said everything was fine. Taken verbatim from `lemma-research`. */
        folder: "lemma-research",
        updated: "2026-09-16T11:05:00Z",
        md: [
            "---",
            "name: lemma-research",
            "description: Run rigorous, source-backed research in an existing Lemma pod, preserving the path from question to source to evidence to claim. Use for investigations, literature or market scans, policy and product research, fact-checking, comparisons, current-information questions, and updates to prior research that require pod files, workspace material, or web sources; exact citations; explicit freshness and conflict handling; and a durable memo or source pack.",
            "---",
            "",
            "# Lemma Research",
            "",
            "Build an auditable investigation, not a disposable answer.",
        ].join("\n"),
    },
    {
        folder: "weekly-review",
        updated: "2026-09-15T09:20:00Z",
        md: [
            "---",
            "name: weekly-review",
            "description: Pull the week's launch numbers together and write the Friday note, including the one thing that slipped.",
            "---",
            "",
            "# Weekly review",
            "",
            "Run this on Friday morning, before anybody asks for it.",
            "",
            "## What to gather",
            "",
            "- The five competitor pages, and anything that changed on them",
            "- Open loops from last week's note that are still open",
            "- Whatever landed in `/launch` since Monday",
            "",
            "## How to write it",
            "",
            "Lead with what slipped. A review that opens with what went well is a review",
            "nobody finishes reading, and the thing that slipped is the reason anybody",
            "asked for the note in the first place.",
            "",
            "Keep it under a screen. Link the detail rather than pasting it.",
        ].join("\n"),
    },
    {
        folder: "competitor-watch",
        updated: "2026-09-12T14:05:00Z",
        md: [
            "---",
            "name: competitor-watch",
            'description: "Check the five named competitors and say what moved: pricing, positioning, or people."',
            "---",
            "",
            "# Competitor watch",
            "",
            "Five names, in `/launch/competitors.md`. Read each one's pricing page and",
            "changelog, then say what moved — not that you looked.",
            "",
            "Nothing moved is a real answer and a short one. Say it and stop.",
        ].join("\n"),
    },
    {
        /* Frontmatter with a `name` and no `description`. The loader raises on
           it, so this skill does not exist as far as the teammate is
           concerned — and the card is the only place anybody would find out. */
        folder: "quiet-hours",
        updated: "2026-08-30T11:00:00Z",
        md: [
            "---",
            "name: quiet-hours",
            "---",
            "",
            "# Quiet hours",
            "",
            "Do not message anybody between 19:00 and 08:00 in their own timezone.",
            "Queue it and send it in the morning.",
        ].join("\n"),
    },
    {
        /* Renamed in the frontmatter, not on disk. `skill_md.parent.name !=
           name` raises, and nothing else in this product would ever say so. */
        folder: "launch-checklist",
        updated: "2026-09-02T16:40:00Z",
        md: [
            "---",
            "name: launch-check",
            "description: Everything that has to be true before a launch post goes out.",
            "---",
            "",
            "# Launch checklist",
            "",
            "Assets cut, quote approved, partner list confirmed, links live.",
        ].join("\n"),
    },
    {
        /* The folder is there and the file is not readable. A card still has
           to appear: a skill that vanishes because one request failed is
           indistinguishable from a skill nobody wrote. */
        folder: "pricing-notes",
        updated: "2026-07-21T08:15:00Z",
        md: null,
    },
];

/** Edits made in sample mode, so a document you typed into stays typed into.
 *
 *  Sample mode is the only view anybody has without a session, so a feature
 *  that is inert here is a feature nobody can look at. It lives for as long as
 *  the tab does, which is the honest lifetime: there is no pod behind it to
 *  remember anything.
 */
const SAMPLE_EDITS = new Map<string, string>();

/** The sample file tree, before anything was typed into it. */
async function sampleFile(path: string): Promise<FileContent> {
    /* Before the markdown fallback below, because a SKILL.md is markdown
       and would otherwise come back as the sample report — which would
       give every skill in the deck the same description, and a healthy
       one at that. */
    const skill = SAMPLE_SKILLS.find(one => path === "/skills/" + one.folder + "/SKILL.md");
    if (skill) {
        await wait(60);
        /* Thrown, not returned empty: this is what an unreadable file does
           to the caller, and the deck has to survive it per card. */
        if (skill.md === null) throw new Error("That file could not be read.");
        return { name: "SKILL.md", path, mime: "text/markdown", size: skill.md.length, kind: "markdown" as const, text: skill.md, appUrl: "https://example.invalid/file" };
    }
    if (path.endsWith(".pdf")) return { name: "Project overview.pdf", path, mime: "application/pdf", size: 750, kind: "pdf" as const, rawUrl: "/sample-document.pdf" };
    if (/\.(mp4|webm|mov|m4v)$/i.test(path)) {
        await wait(60);
        return { name: path.split("/").pop() ?? path, path, mime: "video/mp4", size: 78_374, kind: "video" as const, rawUrl: "/sample-clip.mp4", appUrl: "https://example.invalid/file" };
    }
    if (/\.(png|jpe?g|gif|webp|svg)$/i.test(path)) {
        await wait(60);
        return { name: path.split("/").pop() ?? path, path, mime: "image/png", size: 18_240, kind: "image" as const, rawUrl: "/agent-logos/claudecode.png", appUrl: "https://example.invalid/file" };
    }
    if (/\.(mp3|wav|m4a|ogg|opus)$/i.test(path)) {
        await wait(60);
        return { name: path.split("/").pop() ?? path, path, mime: "audio/mpeg", size: 48_000, kind: "audio" as const, rawUrl: "/sample-clip.mp4", appUrl: "https://example.invalid/file" };
    }
    /* The card for a file this app will not draw, and the reason it will
       not. The reason is the point: one fixed line reading "Preview
       unavailable for this format" is a sentence about the format, and
       wrong about most of the reasons a file lands here. */
    if (/\.log$/i.test(path)) {
        await wait(60);
        return {
            name: path.split("/").pop() ?? path,
            path,
            mime: "text/plain",
            size: 2_517_664,
            kind: "binary" as const,
            note: "Too big to show here · 2.4 MB",
            appUrl: "https://example.invalid/file",
        };
    }
    if (/\.html?$/i.test(path)) {
        await wait(60);
        return {
            name: path.split("/").pop() ?? path,
            path,
            mime: "text/html",
            size: SAMPLE_PAGE.length,
            kind: "html" as const,
            text: SAMPLE_PAGE,
            appUrl: "https://example.invalid/file",
        };
    }
    await wait(60);
    return {
        name: path.split("/").pop() ?? path,
        path,
        mime: "text/markdown",
        size: 82,
        kind: "markdown" as const,
        appUrl: "https://example.invalid/file",
        /* Long enough to judge. Two lines of placeholder could never show a
           measure, a heading scale, a table or the step between them, which
           is why the document surface went so long without any. */
        text: SAMPLE_DOCUMENT,
    };
}

export const fixtureSource: PodSource = {
    label: "sample",
    async listOrgs() {
        await wait(60);
        return belongsToNothing() ? [] : ORGS;
    },

    async myInvitations() {
        await wait(60);
        return belongsToNothing() ? INVITATIONS : [];
    },

    async acceptInvitation(invitationId: string) {
        await wait(220);
        const invite = INVITATIONS.find((one) => one.id === invitationId);
        if (invite && !ORGS.some((org) => org.id === invite.orgId)) ORGS.push({ id: invite.orgId, name: invite.orgName });
        stopPretending();
    },

    async suggestedOrgs() {
        await wait(60);
        return belongsToNothing() ? SUGGESTED : [];
    },

    async joinOrg(orgId: string) {
        await wait(220);
        const found = SUGGESTED.find((org) => org.id === orgId);
        if (found && !ORGS.some((org) => org.id === orgId)) ORGS.push(found);
        stopPretending();
    },

    async createOrg(wanted: NewOrg) {
        await wait(260);
        const made: Org = { id: "made-" + ORGS.length, name: wanted.name };
        /* The only one, because somebody making their first workspace has no
           others — leaving Acme in the list put them straight back into
           somebody else's organization and hid the empty one they had just
           made. A reload puts the sample back as it was. */
        if (belongsToNothing()) ORGS.length = 0;
        ORGS.push(made);
        stopPretending();
        return made;
    },
    async getPod(podId: string) { return PODS.find(pod => pod.id === podId) ?? null; },
    async listPods(orgId: string) {
        await wait(60);
        /* A copy, not the array itself. Handing out the internal one meant
           the query cache already held the very object `createPod` pushes
           into — so after a hire the "new" data and the cached data were the
           same reference, deep-equal by definition, and the rail never
           noticed it had grown a teammate.
         *
         *  Filtered by organization, and it has to be: every sample pod
           belongs to Acme, so ignoring the argument opens a workspace made
           thirty seconds ago with twelve teammates already in it — and the
           empty organization, the one state the arrival path ends in, cannot
           be reached here at all. */
        return PODS.filter((pod) => pod.orgId === orgId);
    },
    async listLibrary(_podId, kind, directory) {
        const updated = "2026-09-14T08:00:00Z";
        if (kind === "tables") return { items: SAMPLE_TABLES.map(table => ({
            id: table.name, name: table.name, kind: "table" as const, path: table.name, updated, detail: table.detail,
        })) };
        if (directory === "/me") return { items: [{ id: "personal-note", name: "My notes.md", kind: "file" as const, path: "/me/notes.md", updated, detail: "Personal notes" }] };
        if (directory === "/skills") return { items: SAMPLE_SKILLS.map(skill => ({ id: "skill-" + skill.folder, name: skill.folder, kind: "folder" as const, path: "/skills/" + skill.folder, updated: skill.updated, detail: "Instructions and supporting resources" })) };
        if (directory.startsWith("/skills/")) return { items: [{ id: "skill-md", name: "SKILL.md", kind: "file" as const, path: directory + "/SKILL.md", updated, detail: "Skill instructions" }] };
        return { items: directory === "/" ? [
            { id: "pdf", name: "Project overview.pdf", kind: "file" as const, path: "/sample-document.pdf", updated, detail: "PDF document" },
            { id: "me", name: "me", kind: "folder" as const, path: "/me", updated, detail: "Personal" },
            { id: "skills", name: "skills", kind: "folder" as const, path: "/skills", updated, detail: "Skills" },
            { id: "report", name: "Weekly report.md", kind: "file" as const, path: "/sample-report.md", updated, detail: "Weekly progress and next steps" },
            { id: "clip", name: "sample-clip.mp4", kind: "file" as const, path: "/videos/sample-clip.mp4", updated, detail: "Video" },
            { id: "page", name: "launch-preview.html", kind: "file" as const, path: "/launch-preview.html", updated, detail: "Web page" },
            { id: "log", name: "run-2026-09-14.log", kind: "file" as const, path: "/run-2026-09-14.log", updated, detail: "Log" },
            { id: "docs", name: "Documents", kind: "folder" as const, path: "/documents", updated, detail: "Folder" },
            { id: "system", name: ".internal", kind: "folder" as const, path: "/.internal", updated, detail: "Internal files" },
        ] : [{ id: "brief", name: "Project brief.md", kind: "file" as const, path: "/documents/brief.md", updated, detail: "Project context" }] };
    },
    async tableColumns(_podId, name) { return sampleTable(name)?.columns ?? []; },
    /* Paged like the real one, fifty at a time. A sample that hands over every
       row at once is a sample where "the first page of four thousand" cannot
       happen, which is the case the view most needs to get right. */
    async tableRows(_podId, name, page) {
        const rows = sampleTable(name)?.rows() ?? [];
        const from = page ? Number(page) : 0;
        const items = rows.slice(from, from + 50);
        const next = from + 50 < rows.length ? String(from + 50) : null;
        return { items, next };
    },
    async tableCount(_podId, name) { return sampleTable(name)?.rows().length ?? null; },
    async tableRecord(_podId, name, id) {
        return sampleTable(name)?.rows().find(row => String(row.id) === id) ?? null;
    },
    async tableShape(_podId, name) {
        const table = sampleTable(name);
        return table ? sampleShape(table) : null;
    },
    async tableShapes() { return SAMPLE_TABLES.map(sampleShape); },
    async referencing(_podId, table, column, id, limit) {
        return (sampleTable(table)?.rows() ?? []).filter(row => String(row[column]) === id).slice(0, limit);
    },
    /* The sample datastore answers every query with the same two rows. It is
       a shape to look at, not a result to believe, which is what the sample
       source is for. */
    async runQuery() {
        await wait(60);
        return { items: [
            { vendor: "Northfield", q1_total: 84200, invoices: 12 },
            { vendor: "Studio Eight", q1_total: 61050, invoices: 9 },
        ] as Record<string, unknown>[], truncated: false };
    },

    async listTabs() {
        await wait(40);
        const tabs: Tab[] = [
            { id: "conversation", kind: "conversation", label: "Conversation" },
            { id: "apps", kind: "apps", label: "Apps" },
            { id: "app:sample", kind: "app", label: "Sample app", url: "/sample-workspace.html", status: "sample" },
            { id: "library", kind: "library", label: "Library" },
            { id: "profile", kind: "profile", label: "Profile" },
        ];
        return tabs;
    },
    /* Hiring is the one thing the sample source does for real — in memory,
       and only until the page reloads. It exists so the flow that is hardest
       to judge from a screenshot, and the only one with a reveal in it, can be
       walked through with no session at all. Everything else still refuses,
       because writing a message or adding a person to nobody teaches nothing. */
    async createPod(orgId: string, name: string, description?: string) {
        await wait(420);
        const id = "sample-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + PODS.length;
        const pod: Pod = {
            id,
            orgId,
            name,
            iconUrl: null,
            teammate: { name, initials: name.slice(0, 2).toUpperCase(), iconUrl: null },
            subtitle: description?.trim() || "just you",
            members: [],
            waiting: "",
        };
        PODS.push(pod);
        return pod;
    },
    async shareFile(_podId: string, _path: string, options?: { expiresSeconds?: number; maxHits?: number }) {
        await wait(260);
        const code = "sample" + Math.random().toString(36).slice(2, 8);
        return {
            rawUrl: "https://api.example/s/" + code,
            readUrl: (typeof window === "undefined" ? "" : window.location.origin) + "/d/" + code,
            code,
            expiresAt: new Date(Date.now() + (options?.expiresSeconds ?? 10800) * 1000).toISOString(),
            maxHits: Math.min(options?.maxHits ?? 50, 100),
        };
    },
    async setPodIcon(podId: string, iconUrl: string | null) {
        await wait(140);
        const pod = PODS.find((candidate) => candidate.id === podId);
        if (pod) {
            pod.iconUrl = iconUrl;
            pod.teammate = { ...pod.teammate, iconUrl };
        }
    },
    /* Real, in memory, for the reason hiring is: a rename is judged by what
       happens to the rail, the header and the badge in the same beat, and
       none of that can be read off a refusal. The teammate's own name follows
       the pod's, because that is what the live source does — a pod with no
       front agent answers under its own name. */
    async renamePod(podId: string, name: string) {
        await wait(260);
        const clean = name.trim();
        if (!clean) throw new Error("A teammate needs a name.");
        const pod = PODS.find((candidate) => candidate.id === podId);
        if (!pod) return;
        const wore = pod.teammate.name === pod.name;
        pod.name = clean;
        if (wore) pod.teammate = { ...pod.teammate, name: clean, initials: clean.slice(0, 2).toUpperCase() };
    },
    async uploadIcon(file: File) {
        await wait(200);
        return await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = () => reject(new Error("That file would not read."));
            reader.readAsDataURL(file);
        });
    },
    async listSurfaces() {
        await wait(40);
        return [...SURFACES];
    },
    async getSurface() { throw new Error("Channel configuration is available in a connected workspace."); },
    async surfaceSetup() { throw new Error("Setup status is available in a connected workspace."); },
    async surfaceGuide() { throw new Error("Setup instructions are available in a connected workspace."); },
    async surfaceChannels() { return { channels: [] }; },
    async updateSurface() { throw new Error("Channel configuration is available in a connected workspace."); },
    async createSurfaceAccount() { throw new Error("Connect accounts in a connected workspace."); },
    async listConnectable() {
        await wait(90);
        return [...CONNECTABLE].map(readConnectable).filter((entry): entry is Connectable => entry !== null).sort(byEffort);
    },
    async connectSystem(_podId: string, platform: string) {
        await wait(900);
        const made: Surface = {
            id: "s-" + platform.toLowerCase(),
            platform,
            name: platform.toLowerCase(),
            mine: true,
            agentName: "Marketing",
            handle: SAMPLE_HANDLE[platform] ?? platform.toLowerCase(),
            email: platform === "RESEND" ? SAMPLE_HANDLE.RESEND : undefined,
            active: true,
        };
        SURFACES = [...SURFACES, made];
        return made;
    },
    async listRuntimes() {
        await wait(110);
        return RUNTIMES.map(readRuntime).filter((entry): entry is Runtime => entry !== null);
    },
    async defaultRuntime() {
        await wait(60);
        return { runtimeId: "system:lemma", model: "" };
    },
    async listComputers() {
        await wait(140);
        return COMPUTERS.map(readComputer)
            .filter((entry): entry is Computer => entry !== null)
            .map((computer) => ({
                ...computer,
                agents: (AGENTS_ON[computer.id] ?? [])
                    .map(readLocalAgent)
                    .filter((agent): agent is LocalAgent => agent !== null),
            }));
    },
    async addProviderKey(_orgId: string, key) {
        await wait(500);
        RUNTIMES = [...RUNTIMES, {
            /* The platform mints the id, and two keys may share a name —
               deriving one from the name collides with itself. */
            id: "org:key-" + (RUNTIMES.length + 1),
            name: key.name, kind: "MODEL_PROVIDER", scope: "ORGANIZATION", status: "ACTIVE",
            default_model_name: key.models[0] ?? null,
            model_catalog: key.models.map((name) => ({
                name,
                capabilities: key.protocol === "anthropic" || key.visionModels?.includes(name)
                    ? ["TEXT", "TOOLS", "VISION"]
                    : ["TEXT", "TOOLS"],
            })),
        }];
    },
    async addLocalAgent(_orgId: string, harnessId: string, agent) {
        await wait(500);
        const found = Object.values(AGENTS_ON).flat().map(readLocalAgent).find((one) => one?.id === harnessId);
        RUNTIMES = [...RUNTIMES, {
            id: "me:" + harnessId, name: agent.name, kind: "HARNESS", status: "ACTIVE",
            scope: agent.shared ? "ORGANIZATION" : "PERSONAL",
            harness_id: harnessId, availability_status: "READY",
            default_model_name: agent.model || null,
            config: { config_selections: agent.selections },
            metadata: { harness_key: found?.harness ?? "" },
            model_catalog: (found?.models ?? []).map((model) => ({ name: model.name, display_name: model.label })),
        }];
    },
    async updateLocalAgent(_orgId: string, runtimeId: string, changes) {
        await wait(400);
        RUNTIMES = RUNTIMES.map((raw) => {
            const entry = raw as { id?: string; default_model_name?: string | null; config?: Record<string, unknown> };
            if (entry.id !== runtimeId) return raw;
            return {
                ...entry,
                ...(changes.default_model_name !== undefined ? { default_model_name: changes.default_model_name } : {}),
                ...(changes.config_selections ? { config: { ...entry.config, config_selections: changes.config_selections } } : {}),
            };
        });
    },
    async archiveRuntime(_orgId: string, runtimeId: string) {
        await wait(300);
        RUNTIMES = RUNTIMES.map((raw) =>
            (raw as { id?: string }).id === runtimeId ? { ...(raw as object), status: "DISABLED" } : raw);
    },
    async restoreRuntime(_orgId: string, runtimeId: string) {
        await wait(300);
        RUNTIMES = RUNTIMES.map((raw) =>
            (raw as { id?: string }).id === runtimeId ? { ...(raw as object), status: "ACTIVE" } : raw);
    },
    async getPodRuntime(podId: string) {
        await wait(80);
        return readChoice(POD_RUNTIME[podId]);
    },
    async setPodRuntime(podId: string, choice) {
        await wait(400);
        if (choice) POD_RUNTIME[podId] = { profile_id: choice.runtimeId, model_name: choice.model };
        else delete POD_RUNTIME[podId];
    },
    async getOrgJoin(orgId: string) {
        await wait(80);
        return ORG_JOIN[orgId] ?? { policy: "invited" as const, domain: "" };
    },
    async setOrgJoin(orgId: string, join) {
        await wait(320);
        ORG_JOIN[orgId] = join;
    },
    async getPodJoin(podId: string) {
        await wait(80);
        return POD_JOIN[podId] ?? "invited";
    },
    async setPodJoin(podId: string, policy) {
        await wait(320);
        POD_JOIN[podId] = policy;
    },
    async myJoinRequest(podId: string) {
        await wait(60);
        return ASKED[podId] ?? null;
    },
    async askToJoin(podId: string) {
        await wait(320);
        /* The sample source answers the way the platform would, which is the
           only part of this worth imitating: an open door admits you and a
           shut one makes you wait. A fixture that always answered `pending`
           would leave the admitted path undrawn and untested. */
        const open = (POD_JOIN[podId] ?? "invited") !== "invited";
        const request: JoinRequest = {
            id: "jr-mine",
            podId,
            standing: open ? "approved" : "pending",
            name: "Sample user",
            email: "you@acme.test",
            askedAt: new Date().toISOString(),
        };
        ASKED[podId] = request;
        /* An open door really opens: the membership the platform would have
           made is made here too, so the rail grows and the shell walks in.
           A fixture that returned `approved` and left the list alone would
           strand somebody on a screen saying they were in. */
        if (open && !PODS.some((one) => one.id === podId)) {
            PODS.push({
                id: podId,
                orgId: "acme",
                name: "Open teammate",
                iconUrl: null,
                teammate: { name: "Open teammate", initials: "OT", iconUrl: null },
                subtitle: "",
                members: [],
                waiting: "",
            });
        }
        return request;
    },
    async listJoinRequests(podId: string) {
        await wait(110);
        return KNOCKING.filter((one) => one.podId === podId && one.standing === "pending");
    },
    async admitToPod(_podId: string, requestId: string) {
        await wait(300);
        const at = KNOCKING.findIndex((one) => one.id === requestId);
        if (at < 0) throw new Error("That ask is no longer waiting.");
        const admitted: JoinRequest = { ...KNOCKING[at], standing: "approved" };
        KNOCKING.splice(at, 1);
        return admitted;
    },
    async listConnectors() {
        await wait(120);
        return CONNECTORS.map(readConnector).filter((entry): entry is Connector => entry !== null)
            .sort((a, b) => a.title.localeCompare(b.title));
    },
    async listAccounts() {
        await wait(90);
        settleAccount();
        return ACCOUNTS.map(readAccount).filter((entry): entry is ConnectorAccount => entry !== null);
    },
    async disconnectAccount(_orgId: string, accountId: string) {
        await wait(400);
        ACCOUNTS = ACCOUNTS.filter((raw) => (raw as { id?: string }).id !== accountId);
    },
    async listMySurfaces() {
        await wait(80);
        /* Another teammate already answers on Slack — the case that made a
           "Ready now" into a failed click. */
        return [
            { platform: "SLACK", podId: "r2", name: "slack" },
            { platform: "RESEND", podId: "r1", name: "email" },
        ];
    },
    async slackManifest(agentName: string) {
        await wait(250);
        return {
            display_information: { name: agentName, description: "Answers as " + agentName + " in Slack." },
            features: { bot_user: { display_name: agentName, always_online: true } },
            oauth_config: {
                scopes: { bot: ["app_mentions:read", "chat:write", "im:history", "im:read", "im:write"] },
                redirect_urls: ["https://api.example.invalid/connectors/oauth/callback"],
            },
            settings: {
                event_subscriptions: {
                    request_url: "https://api.example.invalid/surfaces/slack/events",
                    bot_events: ["app_mention", "message.im"],
                },
            },
        } as Record<string, unknown>;
    },
    async addCustomApp() {
        await wait(600);
        return "authcfg-sample";
    },
    async startAccount(_orgId: string, connectorId: string) {
        await wait(400);
        accountStartedAt = Date.now();
        accountPending = connectorId;
        /* A stand-in for the provider's consent page. Real enough to walk the
           flow; obviously not Slack. */
        return { authorizeUrl: "/sample-consent.html", before: [] as string[] };
    },
    async findAccount(_orgId: string, connectorId: string) {
        await wait(300);
        settleAccount();
        const found = ACCOUNTS.map(readAccount).find(
            (account) => account?.connectorId === connectorId && account.usable,
        );
        return found?.id ?? "";
    },
    async connectAccount(_podId: string, platform: string) {
        await wait(600);
        const made: Surface = {
            id: "s-" + platform.toLowerCase(),
            platform,
            name: platform.toLowerCase(),
            mine: true,
            agentName: "Marketing",
            handle: SAMPLE_HANDLE[platform] ?? platform.toLowerCase(),
            active: true,
        };
        SURFACES = [...SURFACES, made];
        return made;
    },
    async startGuided() {
        await wait(500);
        guidedStartedAt = Date.now();
        return {
            setupId: "sample-setup",
            launchUrl: "https://t.me/LemmaSetupBot?start=sample",
            managerBot: "@LemmaSetupBot",
            status: "PENDING",
            expiresAt: new Date(Date.now() + 15 * 60_000).toISOString(),
        };
    },
    async checkGuided() {
        await wait(300);
        /* Ready after a few seconds, so the waiting state is a state you can
           actually look at rather than a frame that flashes past. */
        const done = Date.now() - guidedStartedAt > 6_000;
        if (done && !SURFACES.some((surface) => surface.name === "telegram")) {
            SURFACES = [...SURFACES, {
                id: "s-telegram", platform: "TELEGRAM", name: "telegram", mine: true,
                agentName: "Marketing", handle: "@marketing_acme_bot", active: true,
            }];
        }
        return {
            setupId: "sample-setup",
            launchUrl: "https://t.me/LemmaSetupBot?start=sample",
            managerBot: "@LemmaSetupBot",
            status: done ? "READY" : "PENDING",
            botUsername: done ? "@marketing_acme_bot" : undefined,
            botLaunchUrl: done ? "https://t.me/marketing_acme_bot" : undefined,
            expiresAt: new Date(guidedStartedAt + 15 * 60_000).toISOString(),
        };
    },
    async disconnect(_podId: string, surfaceName: string) {
        await wait(400);
        SURFACES = SURFACES.filter((surface) => surface.name !== surfaceName);
    },
    /* Read off the pod it was asked about rather than answered from a
       constant. One roster was returned for every teammate, so every profile
       in the sample listed a colleague called Marketing — and a rename, which
       is judged by whether the new name reaches every corner, reached all of
       them but this one. */
    async getPodDetail(podId: string) {
        await wait(40);
        const pod = PODS.find((candidate) => candidate.id === podId);
        const name = pod?.teammate.name ?? "Marketing";
        const initials = pod?.teammate.initials ?? "MA";
        return {
            members: MEMBERS.map((member) => member.kind === "teammate" ? { ...member, name, initials } : member),
            teammate: { name, initials, iconUrl: "📣" },
            subtitle: "with Priya and you",
        };
    },
    async downloadFile(_podId, path) {
        if (path.endsWith(".pdf")) return (await fetch("/sample-document.pdf")).blob();
        return new Blob(["Sample file content"], { type: "text/plain" });
    },
    async readFile(_podId: string, path: string) {
        const file = await sampleFile(path);
        const edited = SAMPLE_EDITS.get(path);
        return edited === undefined ? file : { ...file, text: edited, size: edited.length };
    },
    async writeFile(_podId: string, path: string, text: string) {
        /* Slow enough to see. A write that lands instantly cannot show what
           saving looks like, and that status line is half of what there is to
           judge here. */
        await wait(220);
        SAMPLE_EDITS.set(path, text);
    },
    async widgetEmbedUrl(): Promise<string> {
        throw new Error("The sample source cannot mint an embed URL.");
    },
    async listConversations() {
        await wait(40);
        return [
            { id: "fixture", title: "Monday launch", at: "10:14", kind: "CHAT" },
            { id: "c2", title: "Long report preview and channel layout review", at: "Fri 11 Sept", kind: "TASK" },
            { id: "c3", title: "Design partner shortlist", at: "Thu", kind: "CHAT" },
            { id: "c4", title: "Q1 vendor totals", at: "Wed", kind: "CHAT" },
            { id: "c5", title: "Tracker refresh", at: "Tue", kind: "TASK" },
            { id: "c6", title: "Blog outline", at: "Mon", kind: "CHAT" },
            { id: "c7", title: "Pricing page copy", at: "Sat 18 Jul", kind: "CHAT" },
            { id: "c8", title: "hey", at: "Fri 17 Jul", kind: "CHAT" },
        ];
    },

    /* The sample source has no calls in it — a call needs a live session, and
       inventing one here would put a thread on screen that cannot be opened. */
    async listCallThreads() {
        return [];
    },
    async getConversation(_podId: string, _teammate?: unknown, conversationId?: string | null) {
        await wait(80);
        if (conversationId === NEW_CONVERSATION) {
            return { id: null, title: "", status: null, messages: [] };
        }
        if (conversationId === "c2") return {
            id: "c2", title: "Long report preview", status: "COMPLETED",
            messages: [
                { id: "preview-user", role: "user", kind: "TEXT", sequence: 1, text: "Show the full sample report." },
                { id: "preview-tool", role: "assistant", kind: "TOOL_CALL", sequence: 2,
                    tool_name: "display_resource", tool_args: { type: "WIDGET", name: "Sample report · layout check",
                        content: `<html><head><style>*{box-sizing:border-box}body{margin:0;padding:22px;background:var(--lemma-widget-bg,#fff);color:var(--lemma-widget-text,#111);font:15px/1.6 var(--lemma-widget-font,system-ui)}h2{margin:0 0 4px;font-size:19px;font-weight:500;letter-spacing:-.01em}.sub{margin:0 0 18px;color:var(--lemma-widget-faint,#888);font-size:12.5px}section{padding:14px 16px;margin-bottom:8px;background:var(--lemma-widget-surface,#fff);border:1px solid var(--lemma-widget-border,#e5e5e5);border-radius:var(--lemma-widget-radius,14px)}strong{display:block;margin-bottom:3px;color:var(--lemma-widget-text,#111);font-weight:500}p{margin:0;color:var(--lemma-widget-muted,#555);font-size:13.5px}.pill{float:right;padding:3px 9px;border-radius:999px;font-size:11px;background:var(--lemma-widget-accent-soft,#eee);color:var(--lemma-widget-accent,#333)}button{margin-top:10px;padding:8px 13px;font:inherit;font-size:13px;color:var(--lemma-widget-text,#111);background:var(--lemma-widget-surface,#fff);border:1px solid var(--lemma-widget-border,#e5e5e5);border-radius:999px;cursor:pointer}</style></head><body><h2>Open loops</h2><p class="sub">Pulled live from the pod when this rendered.</p>${[["Send Deepak the updated deck","promise"],["Reply to Rhea on data pod access","reply owed"],["Acknowledge Nikhil's intro","ack owed"],["Dev agreed to a founders call","stalled"],["Introduce Farah to Razorpay","intro owed"]].map(([title, state]) => `<section><span class="pill">${state}</span><strong>${title}</strong><p>Open since last week.</p></section>`).join("")}<button onclick="window.lemma&&window.lemma.compose('Which of these open loops should I close first?')">Ask which to close first</button></body></html>`,

                    } },
                { id: "preview-reply", role: "assistant", kind: "TEXT", sequence: 3, text: "Here’s the sample report. Expand the preview if you need more space." },
            ],
        };
        return { ...CONVERSATION, messages: FULL_HISTORY };
    },
    async getProfile() {
        await wait(80);
        /* The commitments come off the same rows the section draws, so the
           headline's count of standing jobs and the list under it cannot
           disagree — and pausing one in the sample changes both. */
        return {
            ...PROFILE,
            commitments: readSchedules(SCHEDULES).map((job) => ({
                id: job.id,
                title: job.title,
                detail: job.instruction || job.filter,
                cadence: job.trigger,
                since: job.since,
                active: job.active,
                last: job.lastFiredAt ? "recently" : "",
            })),
        };
    },
    async listAgents() {
        await wait(70);
        return agentRows({ items: AGENTS });
    },
    async getAgent(_podId: string, name: string) {
        await wait(60);
        const found = AGENTS.find((agent) => agent.name === name);
        if (!found) throw new Error("No agent called " + name + " here.");
        return readAgentDetail(found);
    },
    /* Real, in memory, for the same reason hiring is: an edit is the one part
       of this view with a dirty state, a validation path and a refusal in it,
       and none of the three can be judged from a screenshot of a form nobody
       may submit. Until the page reloads. */
    async updateAgent(_podId: string, name: string, before: AgentDraft, after: AgentDraft) {
        await wait(420);
        const found = AGENTS.find((agent) => agent.name === name);
        if (!found) throw new Error("No agent called " + name + " here.");
        const patch = agentChanges(before, after);
        AGENTS = AGENTS.map((agent) => (agent === found
            ? { ...agent, ...patch, updated_at: new Date().toISOString() }
            : agent));
        return readAgentDetail(AGENTS.find((agent) => agent.name === name));
    },
    async deleteAgent(_podId: string, name: string) {
        await wait(420);
        AGENTS = AGENTS.filter((agent) => agent.name !== name);
        SURFACES = SURFACES.filter((surface) => surface.agentName !== displayAgentName(name));
    },

    /* Real, in memory, for the same reason the agent edit is: pause, resume,
       retry and create each have a pending state, a refusal and a row that
       changes underneath them, and none of the four can be judged from a
       screenshot of a control nobody may press. Until the page reloads. */
    async listSchedules() {
        await wait(90);
        return readSchedules(SCHEDULES);
    },
    async listScheduleRuns(_podId: string, scheduleId: string) {
        await wait(140);
        return readRuns(SCHEDULE_RUNS[scheduleId] ?? []);
    },
    async setScheduleActive(_podId: string, scheduleId: string, active: boolean) {
        await wait(320);
        const found = SCHEDULES.find((row) => row["id"] === scheduleId);
        if (!found) throw new Error("No schedule with that id here.");
        SCHEDULES = SCHEDULES.map((row) => (row === found
            ? {
                ...row,
                is_active: active,
                /* Resuming clears both, the way the service does: an explicit
                   reactivation resets the failure count, and the derived
                   `paused_by_failures` is false the moment it is active. */
                paused_by_failures: active ? false : row["paused_by_failures"],
                consecutive_failures: active ? 0 : row["consecutive_failures"],
            }
            : row));
        return readSchedule(SCHEDULES.find((row) => row["id"] === scheduleId));
    },
    async retryScheduleRun(_podId: string, scheduleId: string, runId: string) {
        await wait(420);
        const runs = SCHEDULE_RUNS[scheduleId] ?? [];
        const source = runs.find((run) => run["id"] === runId);
        if (!source) throw new Error("No run with that id here.");
        const redrive = {
            ...source,
            id: "redrive-" + runId,
            status: "RECEIVED",
            attempts: 0,
            error_type: null,
            error_code: null,
            redrive_of_run_id: runId,
            created_at: new Date().toISOString(),
            started_at: null,
            completed_at: null,
        };
        runs.unshift(redrive);
        return readRun(redrive);
    },
    async createSchedule(_podId: string, draft: ScheduleDraft) {
        await wait(520);
        const body = createRequest(draft);
        const made = {
            ...body,
            /* The server normalises the name and appends nothing when one was
               given, so the row reads back under the name that was typed. */
            id: "s" + (SCHEDULES.length + 1),
            pod_id: "r1",
            name: String(body.name ?? "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_"),
            is_active: true,
            is_internal: false,
            paused_by_failures: false,
            consecutive_failures: 0,
            last_fired_at: null,
            last_fire_status: null,
            last_error: null,
            created_at: new Date().toISOString(),
            allowed_actions: ["schedule.read", "schedule.update", "schedule.delete"],
        } as Record<string, unknown>;
        SCHEDULES = [...SCHEDULES, made];
        return readSchedule(made);
    },
    async scheduleTargets() {
        await wait(70);
        return [
            ...AGENTS.map((raw) => raw as { name?: string; kind?: string })
                .filter((agent) => Boolean(agent.name))
                .map((agent) => ({
                    kind: "agent" as const,
                    name: agent.name as string,
                    label: displayAgentName(agent.name as string, agent.kind),
                })),
            { kind: "workflow" as const, name: "press_triage", label: "Press triage" },
            { kind: "workflow" as const, name: "launch_digest", label: "Launch digest" },
        ];
    },

    async send() {
        throw new Error("This is the sample source — connect a session to send anything.");
    },
};

/** Sample notifications, so the inbox can be judged without a session.
 *
 *  Deliberately one of each shape the panel has to draw: something awaiting
 *  words, something answered by its own form, something that asked for nothing,
 *  something already answered, and something no channel could carry. Read
 *  directly rather than through `PodSource` — notifications are the caller's
 *  own, not the pod's, so they never belonged on that seam.
 */
export const SAMPLE_NOTIFICATIONS = [
    {
        id: "n-approve",
        title: "Approve the vendor shortlist",
        body: "Three names are ready. A yes or a no on each, and I will send the emails.",
        status: "OPEN",
        delivery_status: "DELIVERED",
        expects_response: true,
        awaiting_response: true,
        responds_through_action: false,
        created_at: new Date(Date.now() - 40 * 60_000).toISOString(),
    },
    {
        id: "n-form",
        title: "Budget sign-off needs the expense form",
        body: "The workflow is paused on a form with amounts and a cost centre.",
        status: "OPEN",
        delivery_status: "DELIVERED",
        expects_response: true,
        awaiting_response: true,
        responds_through_action: true,
        action: { run_id: "sample-run", node_id: "collect" },
        created_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
    },
    {
        id: "n-undeliverable",
        title: "Weekly summary is ready",
        body: "Nothing to do — the write-up is in your files.",
        status: "OPEN",
        delivery_status: "UNDELIVERABLE",
        undeliverable_reason: "No Telegram account is linked to your profile.",
        expects_response: false,
        awaiting_response: false,
        responds_through_action: false,
        created_at: new Date(Date.now() - 26 * 3600_000).toISOString(),
    },
    {
        id: "n-answered",
        title: "Confirm the launch date",
        body: "Marketing asked whether the 24th still holds.",
        status: "RESPONDED",
        delivery_status: "DELIVERED",
        response_summary: "Yes, the 24th holds.",
        read_at: new Date(Date.now() - 2 * 86400_000).toISOString(),
        responded_at: new Date(Date.now() - 2 * 86400_000).toISOString(),
        expects_response: true,
        awaiting_response: false,
        responds_through_action: false,
        created_at: new Date(Date.now() - 2 * 86400_000).toISOString(),
    },
];

/** A workflow run parked on a form, so the inbox's form can be judged.
 *
 *  One field of each kind `buildSchemaFormFields` produces, because that is
 *  what a layout has to survive: a long label beside a short one, a select, a
 *  checkbox that reads left-to-right while everything else reads top-down, and
 *  a number next to a date. A form with three text inputs proves nothing.
 */
export const SAMPLE_WORKFLOW_RUN = {
    id: "sample-run",
    pod_id: "sample",
    workflow_id: "sample-workflow",
    user_id: "sample-user",
    status: "WAITING",
    active_wait: {
        id: "sample-wait",
        run_id: "sample-run",
        pod_id: "sample",
        workflow_id: "sample-workflow",
        node_id: "collect",
        wait_type: "HUMAN",
        status: "WAITING",
        payload: {
            input_schema: {
                type: "object",
                required: ["amount", "cost_centre"],
                properties: {
                    amount: { type: "number", title: "Amount", description: "Total in GBP, excluding VAT." },
                    cost_centre: { type: "string", title: "Cost centre", enum: ["Marketing", "Engineering", "Operations"] },
                    needed_by: { type: "string", format: "date", title: "Needed by" },
                    urgent: { type: "boolean", title: "Treat as urgent", description: "Skips the weekly batch." },
                    notes: { type: "string", title: "Notes", description: "Anything the approver should know." },
                },
            },
            /* Authored order, and deliberately not the alphabet's: urgent sits
               before notes, where sorting by label puts Notes first. Without
               it this fixture could not tell a form that reads the author's
               order from one that ignores it — which is how the notification
               panel went on sorting alphabetically without anybody noticing. */
            ui_schema: { "ui:order": ["amount", "cost_centre", "needed_by", "urgent", "notes"] },
        },
    },
};

/* ── workflows ─────────────────────────────────────────────────────────
 *
 *  Shaped off the real responses rather than off what the views want, which
 *  is the only way the sample source is worth anything: `WorkflowSummaryResponse`
 *  omits the graph and carries derived `node_count`/`node_types`
 *  (app/modules/workflow/api/schemas.py:444); a run summary carries
 *  `workflow_id` and no name; and a wait carries `input_schema` *and*
 *  `ui_schema`, both resolved by the form executor before they were stored
 *  (execution/executors/form.py:49).
 */

/** Four workflows, and each one exists to make a different row happen: one
 *  with a description, one with none (so the shape line has to compose a
 *  sentence out of `node_types`), one paused, and one that has never run. */
export const SAMPLE_WORKFLOWS = [
    {
        id: "wf-budget",
        name: "budget-sign-off",
        description: "Collects an amount and a cost centre, then asks a human to approve it.",
        pod_id: "sample",
        node_count: 5,
        node_types: ["FORM", "AGENT", "DECISION", "FORM", "END"],
        is_active: true,
        updated_at: new Date(Date.now() - 6 * 86400_000).toISOString(),
        allowed_actions: ["read", "run", "update"],
    },
    {
        id: "wf-onboard",
        name: "supplier-onboarding",
        pod_id: "sample",
        node_count: 7,
        node_types: ["FORM", "FUNCTION", "AGENT", "LOOP", "END"],
        is_active: true,
        updated_at: new Date(Date.now() - 2 * 86400_000).toISOString(),
        allowed_actions: ["read", "run"],
    },
    {
        id: "wf-renewals",
        name: "contract-renewals",
        description: "Waits out the notice period, then drafts the letter.",
        pod_id: "sample",
        node_count: 4,
        node_types: ["WAIT_UNTIL", "AGENT", "FORM", "END"],
        is_active: false,
        updated_at: new Date(Date.now() - 40 * 86400_000).toISOString(),
        allowed_actions: ["read"],
    },
    {
        id: "wf-audit",
        name: "quarterly-audit",
        description: "Pulls the ledger and reconciles it against the bank feed.",
        pod_id: "sample",
        node_count: 3,
        node_types: ["FUNCTION", "AGENT", "END"],
        is_active: true,
        updated_at: new Date(Date.now() - 11 * 86400_000).toISOString(),
        allowed_actions: ["read", "run"],
    },
];

/** Runs per workflow *name*, because that is what `runs.list` is keyed on.
 *
 *  One of each status the list has to draw, and — for the failed one — a
 *  `failed_node_id` with an `error` beside it, since naming the node in the
 *  row is the whole reason a run list beats a run count.
 */
export const SAMPLE_WORKFLOW_RUNS: Record<string, unknown[]> = {
    "budget-sign-off": [
        {
            id: "sample-run",
            workflow_id: "wf-budget",
            pod_id: "sample",
            user_id: "sample-user",
            status: "WAITING",
            start_type: "MANUAL",
            current_node_id: "collect",
            started_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
            created_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
        },
        {
            id: "run-failed",
            workflow_id: "wf-budget",
            pod_id: "sample",
            user_id: "sample-user",
            status: "FAILED",
            start_type: "SCHEDULE",
            failed_node_id: "notify-approver",
            error: "The approver expression resolved to 'finance-lead', which is not a pod member id.",
            started_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
            completed_at: new Date(Date.now() - 3 * 86400_000 + 47_000).toISOString(),
            created_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
        },
        {
            id: "run-done",
            workflow_id: "wf-budget",
            pod_id: "sample",
            user_id: "sample-user",
            status: "COMPLETED",
            start_type: "MANUAL",
            started_at: new Date(Date.now() - 9 * 86400_000).toISOString(),
            completed_at: new Date(Date.now() - 9 * 86400_000 + 4 * 3600_000).toISOString(),
            created_at: new Date(Date.now() - 9 * 86400_000).toISOString(),
        },
        {
            id: "run-cancelled",
            workflow_id: "wf-budget",
            pod_id: "sample",
            user_id: "sample-user",
            status: "CANCELLED",
            start_type: "MANUAL",
            started_at: new Date(Date.now() - 20 * 86400_000).toISOString(),
            completed_at: new Date(Date.now() - 20 * 86400_000 + 120_000).toISOString(),
            created_at: new Date(Date.now() - 20 * 86400_000).toISOString(),
        },
        /* A run with no id at all. The list drops it, and the point of it
           being here is that the four above still draw. */
        { workflow_id: "wf-budget", status: "COMPLETED" },
    ],
    "supplier-onboarding": [
        {
            id: "run-onboard",
            workflow_id: "wf-onboard",
            pod_id: "sample",
            user_id: "sample-user",
            /* RUNNING with an AGENT wait: the status field alone would say
               "Running" for something that has been parked on an agent for
               half an hour (domain/run.py:41). */
            status: "RUNNING",
            start_type: "MANUAL",
            current_node_id: "check-companies-house",
            started_at: new Date(Date.now() - 31 * 60_000).toISOString(),
            created_at: new Date(Date.now() - 31 * 60_000).toISOString(),
        },
    ],
    "contract-renewals": [],
    "quarterly-audit": [],
};

/** Runs opened, keyed by id. `runs.list` returns summaries — no
 *  `step_history`, no `active_wait`, no `execution_context` — so opening one
 *  is a second request and a different shape. */
export const SAMPLE_RUN_DETAIL: Record<string, unknown> = {
    "sample-run": {
        ...SAMPLE_WORKFLOW_RUN,
        workflow_id: "wf-budget",
        start_type: "MANUAL",
        current_node_id: "collect",
        started_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
        created_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
        execution_context: {
            start: { started_by: "sample-user", trigger: "MANUAL" },
            "pull-ledger": { rows: 184, through: "2026-09-18" },
        },
        step_history: [
            {
                step_index: 0,
                node_id: "pull-ledger",
                status: "COMPLETED",
                started_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
                completed_at: new Date(Date.now() - 27 * 3600_000 + 2_400).toISOString(),
                output_data: { rows: 184, through: "2026-09-18" },
            },
            {
                step_index: 1,
                node_id: "collect",
                status: "WAITING",
                started_at: new Date(Date.now() - 27 * 3600_000 + 2_500).toISOString(),
            },
        ],
    },
    "run-failed": {
        id: "run-failed",
        workflow_id: "wf-budget",
        pod_id: "sample",
        user_id: "sample-user",
        status: "FAILED",
        start_type: "SCHEDULE",
        failed_node_id: "notify-approver",
        error: "The approver expression resolved to 'finance-lead', which is not a pod member id.",
        started_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
        completed_at: new Date(Date.now() - 3 * 86400_000 + 47_000).toISOString(),
        created_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
        execution_context: { start: { trigger: "SCHEDULE" }, "pull-ledger": { rows: 0 } },
        step_history: [
            {
                step_index: 0,
                node_id: "pull-ledger",
                status: "COMPLETED",
                started_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
                completed_at: new Date(Date.now() - 3 * 86400_000 + 300).toISOString(),
                /* Sub-second, so the step list has to say "under a second"
                   rather than print a zero that reads as "skipped". */
                output_data: { rows: 0 },
            },
            {
                step_index: 1,
                node_id: "notify-approver",
                status: "FAILED",
                started_at: new Date(Date.now() - 3 * 86400_000 + 400).toISOString(),
                completed_at: new Date(Date.now() - 3 * 86400_000 + 47_000).toISOString(),
                error: "assignee expression resolved to 'finance-lead', which is not a pod member id",
            },
            /* A step that arrived as nonsense. It still draws, because hiding
               it would renumber the history somebody is reading. */
            "not a step",
        ],
    },
    "run-onboard": {
        id: "run-onboard",
        workflow_id: "wf-onboard",
        pod_id: "sample",
        user_id: "sample-user",
        status: "RUNNING",
        start_type: "MANUAL",
        current_node_id: "check-companies-house",
        started_at: new Date(Date.now() - 31 * 60_000).toISOString(),
        created_at: new Date(Date.now() - 31 * 60_000).toISOString(),
        /* An AGENT wait on a RUNNING run: the wait row is the only place that
           says what it is really stuck on. */
        active_wait: {
            id: "wait-agent",
            run_id: "run-onboard",
            workflow_id: "wf-onboard",
            pod_id: "sample",
            node_id: "check-companies-house",
            wait_type: "AGENT",
            status: "ACTIVE",
            external_ref: "conv-88120",
            payload: {},
            created_at: new Date(Date.now() - 30 * 60_000).toISOString(),
        },
        execution_context: { start: { trigger: "MANUAL" } },
        step_history: [
            {
                step_index: 0,
                node_id: "intake",
                status: "COMPLETED",
                started_at: new Date(Date.now() - 31 * 60_000).toISOString(),
                completed_at: new Date(Date.now() - 31 * 60_000 + 900).toISOString(),
                output_data: "Riverbend Supplies Ltd, company number 09912844",
            },
            {
                step_index: 1,
                node_id: "check-companies-house",
                status: "RUNNING",
                started_at: new Date(Date.now() - 30 * 60_000).toISOString(),
                external_ref: "conv-88120",
            },
        ],
    },
    "run-done": {
        id: "run-done",
        workflow_id: "wf-budget",
        pod_id: "sample",
        user_id: "sample-user",
        status: "COMPLETED",
        start_type: "MANUAL",
        started_at: new Date(Date.now() - 9 * 86400_000).toISOString(),
        completed_at: new Date(Date.now() - 9 * 86400_000 + 4 * 3600_000).toISOString(),
        created_at: new Date(Date.now() - 9 * 86400_000).toISOString(),
        execution_context: { start: { trigger: "MANUAL" }, collect: { amount: 2400, cost_centre: "Marketing" } },
        step_history: [
            {
                step_index: 0,
                node_id: "collect",
                status: "COMPLETED",
                started_at: new Date(Date.now() - 9 * 86400_000).toISOString(),
                completed_at: new Date(Date.now() - 9 * 86400_000 + 4 * 3600_000 - 1000).toISOString(),
                output_data: { amount: 2400, cost_centre: "Marketing", urgent: false },
            },
            {
                step_index: 1,
                node_id: "approve",
                status: "COMPLETED",
                started_at: new Date(Date.now() - 9 * 86400_000 + 4 * 3600_000 - 900).toISOString(),
                completed_at: new Date(Date.now() - 9 * 86400_000 + 4 * 3600_000).toISOString(),
                output_data: { approved: true },
            },
        ],
    },
};

/** One workflow opened, keyed by *name*, because `workflows.get` is.
 *
 *  `WorkflowSummaryResponse` omits the graph on purpose (api/schemas.py:444),
 *  so these are the second request: `nodes`, `edges` and `start`, shaped the
 *  way `WorkflowResponse` sends them (api/schemas.py:414).
 *
 *  Three graphs the walk has to get right and one it has to survive. The
 *  linear one to prove the order is the wiring's rather than the payload's;
 *  the branching one because a decision's arms live in `config.rules`, not in
 *  `edges`, and a walk that misses that reports every arm as unreachable; the
 *  loop because its body edges back to it and a naive walk never returns. The
 *  fourth is a graph nobody could save through the API: a step nothing points
 *  at, a node id that is not there, and a row of nonsense where a node should
 *  be.
 *
 *  And no `entry_node_id` on any of them, which is not an omission —
 *  `WorkflowResponse` does not carry it. The backend computes it at save time
 *  and keeps it (domain/workflow.py:40); the wire shape has `nodes`, `edges`
 *  and `start` and nothing that says which node is first. Deriving it is the
 *  view's problem, so it had better be the sample's problem too.
 */
export const SAMPLE_WORKFLOW_SHAPES: Record<string, unknown> = {
    "budget-sign-off": {
        id: "wf-budget",
        name: "budget-sign-off",
        description: "Collects an amount and a cost centre, then asks a human to approve it.",
        pod_id: "sample",
        is_active: true,
        allowed_actions: ["read", "run", "update"],
        start: { type: "MANUAL", config: null },
        /* Out of order on purpose: `nodes` is a list, and the point of the
           walk is that the wiring decides the order rather than whatever the
           author happened to save last. */
        nodes: [
            {
                id: "notify-approver",
                type: "FORM",
                label: "Approval",
                position: { x: 620, y: 40 },
                config: {
                    input_schema: {
                        type: "object",
                        required: ["approved"],
                        properties: {
                            approved: { type: "boolean", title: "Approved" },
                            note: { type: "string", title: "Note" },
                        },
                    },
                    assignee_pod_member_id_expression: "start.payload.cost_centre_owner",
                },
            },
            { id: "done", type: "END", label: null, position: { x: 900, y: 40 }, config: {} },
            {
                id: "collect",
                type: "FORM",
                label: "The request",
                position: { x: 40, y: 40 },
                config: {
                    input_schema: {
                        type: "object",
                        required: ["amount", "cost_centre"],
                        properties: {
                            amount: { type: "number", title: "Amount" },
                            cost_centre: { type: "string", title: "Cost centre" },
                            needed_by: { type: "string", format: "date", title: "Needed by" },
                            notes: { type: "string", title: "Notes" },
                        },
                    },
                },
            },
            {
                id: "decide",
                type: "DECISION",
                label: null,
                position: { x: 320, y: 40 },
                /* The branches are here and nowhere else. `edges` holds
                   collect→decide and the two joins back to `done`; the arms
                   themselves are rules (domain/nodes/decision.py:26). */
                config: {
                    rules: [
                        { condition: "collect.amount > `5000`", next_node_id: "notify-approver" },
                        { condition: "collect.amount > `0`", next_node_id: "pay" },
                    ],
                },
            },
            {
                id: "pay",
                type: "FUNCTION",
                label: null,
                position: { x: 620, y: 200 },
                config: {
                    function_name: "raise-purchase-order",
                    input_mapping: {
                        amount: { type: "expression", value: "collect.amount" },
                        cost_centre: { type: "expression", value: "collect.cost_centre" },
                        currency: { type: "literal", value: "GBP" },
                    },
                },
            },
        ],
        edges: [
            { id: "e1", source: "collect", target: "decide", label: null },
            { id: "e2", source: "notify-approver", target: "done", label: null },
            { id: "e3", source: "pay", target: "done", label: null },
        ],
    },

    "supplier-onboarding": {
        id: "wf-onboard",
        name: "supplier-onboarding",
        pod_id: "sample",
        is_active: true,
        allowed_actions: ["read", "run"],
        start: {
            type: "DATASTORE_EVENT",
            config: { table_name: "suppliers", operations: ["INSERT", "UPDATE"] },
        },
        nodes: [
            {
                id: "intake",
                type: "FORM",
                label: null,
                position: { x: 40, y: 40 },
                config: {
                    input_schema: {
                        type: "object",
                        required: ["companies"],
                        properties: { companies: { type: "array", title: "Company numbers" } },
                    },
                },
            },
            /* The loop's body is `child_node_id` and the body's last step
               edges back to the loop — the cycle a naive walk hangs on. */
            {
                id: "each-supplier",
                type: "LOOP",
                label: null,
                position: { x: 320, y: 40 },
                config: { items_path: "intake.companies", item_var_name: "supplier", child_node_id: "check-companies-house" },
            },
            {
                id: "check-companies-house",
                type: "AGENT",
                label: "Look it up",
                position: { x: 320, y: 200 },
                config: {
                    agent_name: "companies-house-lookup",
                    input_mapping: { company_number: { type: "expression", value: "loop.supplier" } },
                },
            },
            {
                id: "record-supplier",
                type: "FUNCTION",
                label: null,
                position: { x: 620, y: 200 },
                config: {
                    function_name: "insert-supplier",
                    input_mapping: { row: { type: "expression", value: "check-companies-house.result" } },
                },
            },
            { id: "confirm-terms", type: "FORM", label: null, position: { x: 620, y: 40 }, config: { input_schema: {} } },
            { id: "done", type: "END", label: null, position: { x: 900, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "intake", target: "each-supplier", label: null },
            { id: "e2", source: "check-companies-house", target: "record-supplier", label: null },
            { id: "e3", source: "record-supplier", target: "each-supplier", label: "next" },
            { id: "e4", source: "each-supplier", target: "confirm-terms", label: null },
            { id: "e5", source: "confirm-terms", target: "done", label: null },
        ],
    },

    "contract-renewals": {
        id: "wf-renewals",
        name: "contract-renewals",
        description: "Waits out the notice period, then drafts the letter.",
        pod_id: "sample",
        is_active: false,
        allowed_actions: ["read"],
        /* A scheduled start carries `schedule_type` and nothing else — the
           times live on pod schedules (domain/start.py:47), which is exactly
           the thing a person reading this needs telling. */
        start: { type: "SCHEDULED", config: { schedule_type: "CRON" } },
        nodes: [
            { id: "wait-out-notice", type: "WAIT_UNTIL", label: null, position: { x: 40, y: 40 }, config: { timeout_seconds: 7 * 86400 } },
            { id: "draft-letter", type: "AGENT", label: null, position: { x: 320, y: 40 }, config: { agent_name: "renewal-drafter" } },
            {
                id: "sign-off",
                type: "FORM",
                label: null,
                position: { x: 620, y: 40 },
                config: { input_schema: { type: "object", properties: { send_it: { type: "boolean", title: "Send it" } } }, assignee_pod_member_id: "sample-member" },
            },
            { id: "done", type: "END", label: null, position: { x: 900, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "wait-out-notice", target: "draft-letter", label: null },
            { id: "e2", source: "draft-letter", target: "sign-off", label: null },
            { id: "e3", source: "sign-off", target: "done", label: null },
        ],
    },

    /** A graph the validator would have refused, which is the point.
     *
     *  `email-legal` is a second step nothing points at, so there is no single
     *  first one and a run cannot start (domain/graph.py:150) — the state a
     *  graph written round the API, or a node deleted underneath one, actually
     *  arrives in. Plus a node that is not an object and an edge to a node id
     *  that is not there, because neither should take the panel down.
     */
    "quarterly-audit": {
        id: "wf-audit",
        name: "quarterly-audit",
        description: "Pulls the ledger and reconciles it against the bank feed.",
        pod_id: "sample",
        is_active: true,
        allowed_actions: ["read", "run"],
        start: {
            type: "EVENT",
            config: { connector_id: "xero", connector_trigger_id: "period_closed", trigger_config: { organisation: "riverbend" } },
        },
        nodes: [
            { id: "pull-ledger", type: "FUNCTION", label: null, position: { x: 40, y: 40 }, config: { function_name: "fetch-ledger" } },
            { id: "reconcile", type: "AGENT", label: null, position: { x: 320, y: 40 }, config: { agent_name: "bank-reconciler" } },
            { id: "done", type: "END", label: null, position: { x: 620, y: 40 }, config: {} },
            { id: "email-legal", type: "FUNCTION", label: "Left behind", position: { x: 40, y: 260 }, config: { function_name: "email-legal" } },
            "a node that is not a node",
        ],
        edges: [
            { id: "e1", source: "pull-ledger", target: "reconcile", label: null },
            { id: "e2", source: "reconcile", target: "done", label: null },
            { id: "e3", source: "reconcile", target: "archive-ledger", label: null },
        ],
    },
};

/** The approval queue: `{wait, run}` pairs, exactly as
 *  `WorkflowRunWaitAssignment` shapes them (api/schemas.py:558).
 *
 *  Two of them, hours apart, because the queue's job is to put what has been
 *  stuck longest at the top and one row proves nothing. The second carries a
 *  `ui_schema` with a `ui:order`: without it the SDK sorts fields
 *  alphabetically by label, so an authored order is only ever preserved by
 *  passing it through.
 */
export const SAMPLE_WAITING = [
    {
        wait: {
            ...SAMPLE_WORKFLOW_RUN.active_wait,
            workflow_id: "wf-budget",
            assigned_pod_member_id: "sample-member",
            status: "ACTIVE",
            payload: {
                ...SAMPLE_WORKFLOW_RUN.active_wait.payload,
                ui_schema: { "ui:order": ["amount", "cost_centre", "needed_by", "urgent", "notes"] },
            },
            created_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
        },
        run: {
            id: "sample-run",
            workflow_id: "wf-budget",
            pod_id: "sample",
            user_id: "sample-user",
            status: "WAITING",
            start_type: "MANUAL",
            started_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
            created_at: new Date(Date.now() - 27 * 3600_000).toISOString(),
        },
    },
    {
        wait: {
            id: "wait-supplier",
            run_id: "run-supplier",
            workflow_id: "wf-onboard",
            pod_id: "sample",
            node_id: "confirm-terms",
            wait_type: "HUMAN",
            status: "ACTIVE",
            assigned_pod_member_id: "sample-member",
            created_at: new Date(Date.now() - 95 * 60_000).toISOString(),
            payload: {
                input_schema: {
                    type: "object",
                    required: ["terms_ok"],
                    properties: {
                        terms_ok: { type: "boolean", title: "The terms are acceptable" },
                        payment_days: { type: "number", title: "Payment days", default: 30 },
                        contact_email: { type: "string", format: "email", title: "Contact" },
                    },
                },
                /* Authored order is boolean, number, email — which the
                   alphabet would rearrange to Contact, Payment days, The
                   terms are acceptable. */
                ui_schema: { "ui:order": ["terms_ok", "payment_days", "contact_email"] },
            },
        },
        run: {
            id: "run-supplier",
            workflow_id: "wf-onboard",
            pod_id: "sample",
            user_id: "sample-user",
            status: "WAITING",
            start_type: "MANUAL",
            started_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
            created_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
        },
    },
];
