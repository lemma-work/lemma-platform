// Seeds a local dev stack with one Lemma that has a bit of everything the
// phone shows, so every screen can be checked against live data:
//
//   node scripts/seed-local.mjs [api] [email]
//
// Defaults: http://localhost:8761 and mobile-check@example.com, an account
// made by signing up on the local stack. The password is the scenario
// harness's test password (tests/scenarios/harness/steps/identity.py); this
// is for a local stack only, never a hosted one. Run it again at any time:
// what is already there is left as it is. Chats are scripted for the mock
// model, so the stack must run with E2E_LLM_MODE=mock.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const API = (process.argv[2] ?? "http://localhost:8761").replace(/\/$/, "");
const EMAIL = process.argv[3] ?? "mobile-check@example.com";
if (!/^https?:\/\/(localhost|127\.0\.0\.1|10\.0\.2\.2)(:\d+)?$/.test(API)) throw new Error("Local stacks only.");
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const PASSWORD = /DEFAULT_PASSWORD = "([^"]+)"/.exec(readFileSync(resolve(ROOT, "tests/scenarios/harness/steps/identity.py"), "utf8"))[1];

let token = null;

async function call(method, path, body, headers = {}) {
    const form = body instanceof FormData;
    const response = await fetch(API + path, {
        method,
        headers: {
            Accept: "application/json",
            ...(body !== undefined && !form ? { "Content-Type": "application/json" } : {}),
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...headers,
        },
        body: body === undefined ? undefined : form ? body : JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) throw new Error(`${method} ${path} -> ${response.status} ${text.slice(0, 300)}`);
    return { json: text ? JSON.parse(text) : null, headers: response.headers };
}

const items = (json) => (Array.isArray(json) ? json : json?.items ?? []);

async function step(label, work) {
    try {
        const said = await work();
        console.log(`✓ ${label}${said ? `: ${said}` : ""}`);
    } catch (error) {
        console.log(`✗ ${label}: ${error.message}`);
    }
}

const signin = await call("POST", "/st/auth/signin", { formFields: [{ id: "email", value: EMAIL }, { id: "password", value: PASSWORD }] }, { rid: "emailpassword", "st-auth-mode": "header" });
token = signin.headers.get("st-access-token");
if (!token) throw new Error(`Could not sign in as ${EMAIL}. Sign up on the local stack first.`);
const me = (await call("GET", "/users/me")).json;
const org = items((await call("GET", "/organizations")).json)[0];
if (!org) throw new Error("No organization: make one on the local stack's web app first.");
let pod = items((await call("GET", `/organizations/${org.id}/pods`)).json).find((one) => one.name === "Support");
if (!pod) pod = (await call("POST", "/pods", { name: "Support", organization_id: org.id, description: "Answers customers." })).json;
const P = `/pods/${pod.id}`;
console.log(`Lemma "${pod.name}" (${pod.id}) in ${org.name}, as ${me.email ?? EMAIL}`);

/* ---------- Files ---------- */

function png(size) {
    /* The Lemma mark's three rising bars, violet on white. */
    const rows = [];
    for (let y = 0; y < size; y += 1) {
        const row = Buffer.alloc(1 + size * 3);
        for (let x = 0; x < size; x += 1) {
            const u = (x / size) * 32, v = (y / size) * 32;
            const bar = [[4, 19, 5, 10], [13, 12, 5, 17], [22, 3, 5, 26]].some(([bx, by, bw, bh]) => u >= bx && u <= bx + bw && v >= by && v <= by + bh);
            row.set(bar ? [0x5a, 0x3f, 0xd4] : [255, 255, 255], 1 + x * 3);
        }
        rows.push(row);
    }
    const crc = (bytes) => { let c = ~0; for (const b of bytes) { c ^= b; for (let k = 0; k < 8; k += 1) c = (c >>> 1) ^ (0xedb88320 & -(c & 1)); } return ~c >>> 0; };
    const chunk = (type, data) => { const out = Buffer.alloc(12 + data.length); out.writeUInt32BE(data.length, 0); out.write(type, 4, "ascii"); data.copy(out, 8); out.writeUInt32BE(crc(out.subarray(4, 8 + data.length)), 8 + data.length); return out; };
    const header = Buffer.alloc(13); header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4); header[8] = 8; header[9] = 2;
    return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", header), chunk("IDAT", deflateSync(Buffer.concat(rows))), chunk("IEND", Buffer.alloc(0))]);
}

function pdf(...pages) {
    /* One page per list of lines; the font is the last object. */
    const font = 3 + pages.length * 2;
    const kids = pages.map((_, index) => `${3 + index * 2} 0 R`).join(" ");
    const objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        `<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`,
        ...pages.flatMap((lines, index) => {
            const text = lines.map((line, at) => `BT /F1 ${at === 0 ? 20 : 13} Tf 72 ${720 - at * 24} Td (${line.replace(/[()\\]/g, "")}) Tj ET`).join("\n");
            return [
                `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents ${4 + index * 2} 0 R /Resources << /Font << /F1 ${font} 0 R >> >> >>`,
                `<< /Length ${Buffer.byteLength(text)} >>\nstream\n${text}\nendstream`,
            ];
        }),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ];
    let out = "%PDF-1.4\n";
    const offsets = objects.map((body, index) => { const at = Buffer.byteLength(out); out += `${index + 1} 0 obj\n${body}\nendobj\n`; return at; });
    const xref = Buffer.byteLength(out);
    out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map((at) => `${String(at).padStart(10, "0")} 00000 n \n`).join("")}`;
    out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    return Buffer.from(out);
}

const FILES = [
    ["/", "plan.md", "text/markdown", `---\noutcome: Every customer hears back within a day\nkind: week\ntarget: 90%\nunit: "%"\nmeasure: SELECT 88 AS answered\n---\n\n## What must be true\n- Every customer hears back within a day\n`],
    ["/Notes", "Welcome.md", "text/markdown", "# Welcome\n\nHow Support works, in one page.\n\n## Each morning\n\n- Read the queue\n- Answer what is waiting\n\n| Who | Holds |\n|---|---|\n| Maria | Refunds |\n| Dev | Shipping |\n"],
    ["/Notes", "Prices.html", "text/html", "<h1>Prices</h1><p>Lamps from <b>$60</b>. Shipping is free over $200.</p><ul><li>Desk lamp: $60</li><li>Floor lamp: $140</li></ul>"],
    ["/Notes", "Numbers.csv", "text/csv", "week,answered,asked\n41,31,44\n42,38,41\n43,40,42\n"],
    ["/Notes", "Settings.json", "application/json", JSON.stringify({ reply_within_hours: 24, signature: "The Support team" }, null, 2)],
    ["/Notes", "Brief.pdf", "application/pdf", pdf(["Support brief", "Every customer hears back within a day.", "Refunds over $200 need a yes."])],
    ["/Notes", "Mark.png", "image/png", png(192)],
    /* What a chat shows as files: photos, a document of two pages, a recording. */
    ["/Inbox", "lamp-cracked.jpg", "image/jpeg", readFileSync(resolve(ROOT, "lemma-mobile/assets/sample/lamp-cracked.jpg"))],
    ["/Inbox", "lamp-box.jpg", "image/jpeg", readFileSync(resolve(ROOT, "lemma-mobile/assets/sample/lamp-box.jpg"))],
    ["/Inbox", "lamp-shade.jpg", "image/jpeg", readFileSync(resolve(ROOT, "lemma-mobile/assets/sample/lamp-shade.jpg"))],
    ["/Inbox", "lamp-label.jpg", "image/jpeg", readFileSync(resolve(ROOT, "lemma-mobile/assets/sample/lamp-label.jpg"))],
    ["/Inbox", "Order 4471.pdf", "application/pdf", pdf(
        ["Northwind Lamps", "Order 4471, delivery note", "Desk lamp, opal  x2  $120.00", "Floor lamp, brass  x1  $240.00", "Shade, linen  x1  $60.00", "Total  $420.00"],
        ["Returns", "Damaged in transit: photos are enough.", "Refunds go back to the card within five working days."],
    ), true],
    ["/Inbox", "Voicemail from Maria.m4a", "audio/mp4", readFileSync(resolve(ROOT, "lemma-mobile/assets/sample/voicemail.m4a"))],
    ["/me", "Draft.md", "text/markdown", "# My draft\n\nOnly I can see this page.\n"],
];

for (const [folder, name, type, body, searchable = false] of FILES) {
    await step(`file ${folder === "/" ? "" : folder}/${name}`, async () => {
        /* A folder not made yet is answered 400; the upload makes it. */
        const listed = await call("GET", `${P}/datastore/files?directory_path=${encodeURIComponent(folder)}&limit=200`).then((got) => items(got.json), () => []);
        if (listed.some((one) => one.name === name)) return "already there";
        const form = new FormData();
        form.append("data", new Blob([body], { type }), name);
        form.append("name", name);
        form.append("directory_path", folder);
        /* Searchable only where the server should read it: a document's
           pages are counted when it is. */
        form.append("search_enabled", String(searchable));
        await call("POST", `${P}/datastore/files`, form);
        return "uploaded";
    });
}

/* ---------- Tables ---------- */

const tables = items((await call("GET", `${P}/datastore/tables`)).json).map((one) => one.name);
await step("table projects", async () => {
    if (tables.includes("projects")) return "already there";
    await call("POST", `${P}/datastore/tables`, { name: "projects", enable_rls: false, columns: [{ name: "title", type: "TEXT", required: true }, { name: "owner", type: "TEXT" }] });
    await call("POST", `${P}/datastore/tables/projects/records/bulk/create`, { records: [{ title: "Spring catalogue", owner: "Maria" }, { title: "Returns policy", owner: "Dev" }] });
    return "2 rows";
});
await step("table tasks", async () => {
    if (tables.includes("tasks")) return "already there";
    await call("POST", `${P}/datastore/tables`, {
        name: "tasks",
        enable_rls: false,
        columns: [
            { name: "title", type: "TEXT", required: true },
            { name: "estimate", type: "FLOAT" },
            { name: "due", type: "DATE" },
            { name: "done", type: "BOOLEAN", default: false },
            { name: "status", type: "ENUM", options: ["todo", "doing", "done"], default: "todo" },
            { name: "project_id", type: "UUID", foreign_key: { references: "projects.id" } },
        ],
    });
    const projects = items((await call("GET", `${P}/datastore/tables/projects/records?limit=20`)).json);
    const idOf = (title) => projects.find((one) => one.title === title)?.id;
    const catalogue = idOf("Spring catalogue");
    const returns = idOf("Returns policy");
    const rows = [
        { title: "Photograph the floor lamps", estimate: 3, due: "2026-11-02", status: "doing", project_id: catalogue },
        { title: "Write the lamp descriptions", estimate: 2, due: "2026-11-04", status: "todo", project_id: catalogue },
        { title: "Price the new shades", estimate: 1, due: "2026-11-05", status: "todo", project_id: catalogue },
        { title: "Draft the 30-day rule", estimate: 2, due: "2026-10-28", status: "done", done: true, project_id: returns },
        { title: "Ask Dev about broken-in-transit", estimate: 0.5, due: "2026-10-30", status: "todo", project_id: returns },
    ].map((row) => Object.fromEntries(Object.entries(row).filter(([, value]) => value !== undefined)));
    await call("POST", `${P}/datastore/tables/tasks/records/bulk/create`, { records: rows });
    return `${rows.length} rows`;
});

/* ---------- A workflow waiting on a form, and a schedule ---------- */

await step("workflow approve_expense, waiting on its form", async () => {
    const workflows = items((await call("GET", `${P}/workflows`)).json);
    if (workflows.some((one) => one.name === "approve_expense")) return "already there";
    const member = items((await call("GET", `${P}/members`)).json).find((one) => one.user_id === me.id);
    if (!member) throw new Error("not a member of the Lemma");
    await call("POST", `${P}/workflows`, { name: "approve_expense", mode: "GLOBAL" });
    await call("PUT", `${P}/workflows/approve_expense/graph`, {
        start: { type: "MANUAL" },
        nodes: [
            { id: "ask", type: "FORM", config: { input_schema: { type: "object", properties: { approved: { type: "boolean", title: "Approve the $180 courier refund?" }, note: { type: "string", title: "Note" } }, required: ["approved"] }, assignee_pod_member_id: member.pod_member_id } },
            { id: "done", type: "END" },
        ],
        edges: [{ id: "ask_to_done", source: "ask", target: "done" }],
    });
    const run = (await call("POST", `${P}/workflows/approve_expense/runs`, {})).json;
    return `run ${run.id ?? run.run?.id ?? "started"}`;
});

await step("schedule daily_digest", async () => {
    const schedules = items((await call("GET", `${P}/schedules`)).json);
    if (schedules.some((one) => one.name === "daily_digest")) return "already there";
    await call("POST", `${P}/schedules`, { name: "daily_digest", schedule_type: "TIME", config: { cron: "0 9 * * *" }, agent_name: "POD_DEFAULT", instruction: "Summarize what waited longest yesterday." });
    return "every day at 9";
});

/* ---------- A group ---------- */

await step("group Launch team", async () => {
    const groups = items((await call("GET", `${P}/channels`)).json);
    if (groups.some((one) => one.title === "Launch team")) return "already there";
    const group = (await call("POST", `${P}/channels`, { title: "Launch team", topic: "The spring catalogue", visibility: "PUBLIC", members: [me.id] })).json;
    await call("POST", `${P}/channels/${group.id}/messages`, { text: "Photos are in. Descriptions next." });
    return "made, with one message";
});

/* ---------- Chats, scripted for the mock model ---------- */

const chats = items((await call("GET", `${P}/conversations?pod_id=${pod.id}&limit=100`)).json).map((one) => one.title);

async function scriptedChat(title, script, words) {
    await step(`chat "${title}"`, async () => {
        if (chats.includes(title)) return "already there";
        const made = (await call("POST", `${P}/conversations`, { pod_id: pod.id, title, metadata: { mock_llm_script: script } })).json;
        await call("POST", `${P}/conversations/${made.id}/messages/append`, { content: words });
        return made.id;
    });
}

await scriptedChat("Refund for a broken order", [
    { text: "Their order had 4 lamps and one cracked in transit, which our policy covers. I drafted a refund for the broken lamp.", tool_calls: [{ tool_name: "request_approval", tool_call_id: "approve-refund", args: { tool_name: "exec_command", args: { cmd: "echo refund 240" }, title: "Refund $240 to Northwind", reason: "Stripe · order 4471 · 1 of 4 items" } }] },
    { text: "Refunded. Northwind hears back within the hour." },
], "Northwind says their order arrived broken. What do we do?");

await scriptedChat("Monday launch", [
    { text: "", tool_calls: [{ tool_name: "ask_user", tool_call_id: "ask-day", args: { questions: [{ question: "Which day should the launch post go out?", header: "day", options: [{ label: "Mon 10" }, { label: "Tue 11" }, { label: "Wed 12" }] }] } }] },
    { text: "Tuesday it is. I moved the press list too." },
], "Plan the launch post.");

await scriptedChat("Weekly numbers", [
    {
        text: "Here is the week.",
        tool_calls: [
            { tool_name: "display_resource", tool_call_id: "tc-widget", args: { type: "WIDGET", name: "This week", content: "<div id=\"s\"></div><script>lemma.stats(\"s\", lemma.data.stats)</script>", data: { stats: [{ label: "Answered", value: 40 }, { label: "Asked", value: 42 }, { label: "Waiting", value: 2 }] }, fallback: "Answered 40 of 42; 2 waiting." } },
            { tool_name: "display_resource", tool_call_id: "tc-table", args: { type: "TABLE", name: "tasks", filters: [{ field: "status", op: "eq", value: "todo" }] } },
            { tool_name: "display_resource", tool_call_id: "tc-file", args: { type: "FILE", path: "/Notes/Brief.pdf" } },
        ],
    },
    { text: "The open tasks and the brief are above." },
], "How did the week go?");

await scriptedChat("Northwind's photos", [
    {
        text: "Here is what came in from Northwind.",
        tool_calls: [
            { tool_name: "display_resource", tool_call_id: "tc-p1", args: { type: "FILE", path: "/Inbox/lamp-cracked.jpg" } },
            { tool_name: "display_resource", tool_call_id: "tc-p2", args: { type: "FILE", path: "/Inbox/lamp-box.jpg" } },
            { tool_name: "display_resource", tool_call_id: "tc-p3", args: { type: "FILE", path: "/Inbox/lamp-shade.jpg" } },
            { tool_name: "display_resource", tool_call_id: "tc-p4", args: { type: "FILE", path: "/Inbox/lamp-label.jpg" } },
            { tool_name: "display_resource", tool_call_id: "tc-doc", args: { type: "FILE", path: "/Inbox/Order 4471.pdf" } },
            { tool_name: "display_resource", tool_call_id: "tc-csv", args: { type: "FILE", path: "/Notes/Numbers.csv" } },
            { tool_name: "display_resource", tool_call_id: "tc-voice", args: { type: "FILE", path: "/Inbox/Voicemail from Maria.m4a" } },
        ],
    },
    { text: "Four photos, the order, last week's numbers and Maria's voicemail are above." },
], "What came in from Northwind?\n\nPersonal files available to this run:\n- lamp-cracked.jpg: /Inbox/lamp-cracked.jpg\n- lamp-box.jpg: /Inbox/lamp-box.jpg\n- lamp-shade.jpg: /Inbox/lamp-shade.jpg");

await scriptedChat("Tidy the notes", [
    { text: "I can write the summary into the notes.", tool_calls: [{ tool_name: "request_approval", tool_call_id: "approve-note", args: { tool_name: "pod_write_file", args: { path: "/Notes/Summary.md", content: "# This week\n\n40 of 42 answered.\n", overwrite: true }, title: "Write this week's summary to /Notes", reason: "It is a shared folder" } }] },
    { text: "Written to /Notes/Summary.md." },
], "Put this week's summary in the notes.");

/* Money moving: held for a second on the phone, never one tap. */
await scriptedChat("Refund the courier", [
    { text: "The courier lost one parcel. I can refund Halden for it.", tool_calls: [{ tool_name: "request_approval", tool_call_id: "approve-courier", args: { tool_name: "exec_command", args: { cmd: "echo refund 180 to Halden" }, title: "Refund $180 to Halden", reason: "Stripe · order 4502 · lost by the courier" } }] },
    { text: "Refunded. Halden hears back today." },
], "Halden's parcel never arrived. Sort it out.");

/* No message yet: send one from the phone and the run plays, a few steps
   that each take a moment, so the line under it can be watched. */
await step(`chat "Check the week again"`, async () => {
    if (chats.includes("Check the week again")) return "already there";
    const slow = (seconds, what) => `SELECT (SELECT count(*) FROM tasks WHERE status = '${what}') AS ${what}, pg_sleep(${seconds}) IS NULL AS waited`;
    const script = [
        { text: "", tool_calls: [{ tool_name: "pod_list_files", tool_call_id: "w1", args: { path: "/Notes" } }] },
        { text: "", tool_calls: [{ tool_name: "pod_query", tool_call_id: "w2", args: { sql: slow(3, "todo") } }] },
        { text: "", tool_calls: [{ tool_name: "pod_read_file", tool_call_id: "w3", args: { path: "/Notes/Welcome.md" } }] },
        { text: "", tool_calls: [{ tool_name: "pod_query", tool_call_id: "w4", args: { sql: slow(3, "doing") } }] },
        { text: "Three tasks are still to do and one is under way. The spring catalogue holds most of them: photographing the floor lamps is under way, and the descriptions and the new shades' prices come next. Nothing is late yet; the 30-day rule was finished on time." },
    ];
    const made = (await call("POST", `${P}/conversations`, { pod_id: pod.id, title: "Check the week again", metadata: { mock_llm_script: script } })).json;
    return made.id;
});

/* The same, for three sends: each one plays a run of slow steps. */
await step(`chat "Check the week once more"`, async () => {
    if (chats.includes("Check the week once more")) return "already there";
    const slow = (seconds, what) => `SELECT (SELECT count(*) FROM tasks WHERE status = '${what}') AS ${what}, pg_sleep(${seconds}) IS NULL AS waited`;
    const run = (n) => [
        { text: "", tool_calls: [{ tool_name: "pod_list_files", tool_call_id: `m${n}-1`, args: { path: "/Notes" } }] },
        { text: "", tool_calls: [{ tool_name: "pod_query", tool_call_id: `m${n}-2`, args: { sql: slow(4, "todo") } }] },
        { text: "", tool_calls: [{ tool_name: "pod_read_file", tool_call_id: `m${n}-3`, args: { path: "/Notes/Welcome.md" } }] },
        { text: "Three tasks are still to do and one is under way. Nothing is late yet." },
    ];
    const made = (await call("POST", `${P}/conversations`, { pod_id: pod.id, title: "Check the week once more", metadata: { mock_llm_script: [...run(1), ...run(2), ...run(3)] } })).json;
    return made.id;
});

console.log("Done.");
