import { fileKind } from "../../thread/previews";
import { sampleFirstPage, sampleMediaUrl } from "./sample-media";
import { SAMPLE_PAGES, SAMPLE_SHEET, SAMPLE_SIZES } from "./sample-paths";
import type { Runtime } from "../../thread/runtimes";
import type { ChatSource, ShownFile } from "./source";

/** The chat thread's cards, as sample data: shaped like a real Lemma, never a
 *  real one. The transcripts themselves are `data/sample.ts`'s. */

const TABLES: Record<string, Record<string, unknown>[]> = {
    tickets: [
        { id: "T-1042", customer: "Northwind", subject: "Lamp arrived cracked", status: "refund", opened: "Mon 09:12" },
        { id: "T-1041", customer: "Juniper & Co", subject: "Invoice copy", status: "answered", opened: "Mon 08:40" },
        { id: "T-1039", customer: "Halden", subject: "Change delivery day", status: "answered", opened: "Sun 17:05" },
        { id: "T-1038", customer: "Orchard Lane", subject: "Missing screws", status: "waiting", opened: "Sun 11:30" },
    ],
    tasks: [
        { title: "Photograph the spring range", status: "todo", owner: "Sam", due: "Tue" },
        { title: "Write the catalogue blurbs", status: "todo", owner: "Dev", due: "Wed" },
        { title: "Book the courier", status: "todo", owner: "Ada", due: "Thu" },
    ],
};

const FILES: Record<string, string> = {
    "/policies/refunds.md": [
        "# Refunds",
        "",
        "A customer whose order arrived **damaged** gets a refund for the damaged items, or a replacement if they ask for one.",
        "",
        "- Up to $500 without a second look.",
        "- Above that, a person approves it.",
        "- Photos are enough proof; nobody posts a broken lamp back.",
        "",
        "Refunds go back to the card the order was paid with, within five working days.",
    ].join("\n"),
    "/reports/week-12.md": [
        "# Week 12",
        "",
        "40 of 42 questions answered, 2 waiting on a person. The average first reply took 6 minutes.",
        "",
        "| Day | Asked | Answered |",
        "| --- | ---: | ---: |",
        "| Mon | 9 | 9 |",
        "| Tue | 11 | 10 |",
        "| Wed | 8 | 8 |",
        "| Thu | 14 | 13 |",
    ].join("\n"),
    [SAMPLE_SHEET]: [
        "item,sku,qty,price,arrived",
        "\"Desk lamp, opal\",DL-204,2,$120.00,whole",
        "\"Floor lamp, brass\",FL-118,1,$240.00,cracked shade",
        "\"Shade, linen\",SH-031,1,$60.00,whole",
        "Bulbs (pack of 4),BU-004,1,$12.00,whole",
        "Cable clips,CC-010,6,$1.50,whole",
        "Felt pads,FP-002,8,$0.40,whole",
        "Gift card,GC-025,1,$25.00,whole",
    ].join("\n") + "\n",
};

const RUNTIMES: Runtime[] = [
    {
        id: "sample-runtime",
        name: "Anthropic",
        models: [
            { name: "anthropic/models/claude-sonnet-5-5", label: "Sonnet 5.5" },
            { name: "anthropic/models/claude-opus-5-5", label: "Opus 5.5" },
            { name: "anthropic/models/claude-haiku-5-5", label: "Haiku 5.5" },
        ],
        defaultModel: "anthropic/models/claude-sonnet-5-5",
        archived: false,
    },
];

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function chatSample(): ChatSource {
    const picked = new Map<string, { runtimeId: string; model: string } | null>();
    return {
        async tableRows(_podId, name) {
            await pause(250);
            return { items: TABLES[name] ?? [], truncated: false };
        },
        async runQuery() {
            await pause(250);
            return {
                items: [
                    { week: "Week 10", answered: 31, waiting: 4 },
                    { week: "Week 11", answered: 36, waiting: 3 },
                    { week: "Week 12", answered: 40, waiting: 2 },
                ],
                truncated: false,
            };
        },
        async file(_podId, path): Promise<ShownFile> {
            await pause(200);
            const name = path.split("/").pop() ?? path;
            const kind = fileKind("", path);
            const text = FILES[path] ?? null;
            const url = await sampleMediaUrl(path);
            return {
                name,
                path,
                kind,
                size: text ? text.length : SAMPLE_SIZES[path] ?? 48_213,
                mime: "",
                url,
                text,
                note: text || url || kind === "markdown" || kind === "text" || SAMPLE_PAGES[path] ? null : "Sample data has no file behind it.",
                pages: SAMPLE_PAGES[path] ?? null,
            };
        },
        async firstPage(_podId, path) {
            await pause(300);
            return sampleFirstPage(path);
        },
        converted: async () => null,
        runtimes: async () => RUNTIMES,
        inheritedRuntime: async () => ({ runtimeId: "sample-runtime", model: "" }),
        podRuntime: async () => null,
        agentPinsRuntime: async () => false,
        conversationRuntime: async (_podId, id) => picked.get(id) ?? null,
        async setConversationRuntime(_podId, id, choice) {
            await pause(250);
            picked.set(id, choice);
        },
        rename: async () => pause(200),
        archive: async () => pause(200),
        workingFolder: async (_podId, id) => `/me/conversations/${id}`,
        async upload(_podId, file, directory) {
            await pause(600);
            return { name: file.name, path: `${directory.replace(/\/$/, "")}/${file.name}` };
        },
    };
}
