import type { ColumnSchema, Table } from "lemma-sdk";
import { SAMPLE_LEMMAS } from "../../data/sample";
import type { NewWebWidget, WebWidget } from "@web/data/contacts";
import type { TableOpening } from "@web/data/public-rows";
import { idOf } from "@web/library/record-cache";
import { accessOf, ROWS_PAGE, type Row, type TablesSource } from "./source";

/** Tables and records, as sample data: shaped like a real Lemma, never a real
 *  one. Each table is built to come out as one of the layouts the web
 *  chooses, so all of them can be looked at without a server:
 *
 *  - Support: `tickets` (a board by status), `customers` (cards), `replies`
 *    (a reading view), linked to each other both ways;
 *  - Launch: `launch_checklist` (a checklist with ticks and due dates),
 *    `releases` (a timeline);
 *  - Books: `invoices` (plain rows, two pages of them), `daily_revenue` (a chart).
 *
 *  Held in memory, so a tick, a move, a new row or a delete stays for as long
 *  as the app is open. */

const DAY = 24 * 60 * 60 * 1000;
const [SUPPORT, LAUNCH, , , BOOKS] = SAMPLE_LEMMAS;

type Kind = "TEXT" | "UUID" | "ENUM" | "DATE" | "DATETIME" | "BOOLEAN" | "FLOAT" | "INTEGER";

/** A column. `default` is typed `null` in the generated schema, though the
 *  server sends a column's real default and the SDK's form reads it. */
function column(name: string, type: Kind, extra: Omit<Partial<ColumnSchema>, "default"> & { default?: unknown } = {}): ColumnSchema {
    return { name, type: type as ColumnSchema["type"], ...extra } as ColumnSchema;
}

const SYSTEM = [
    column("id", "UUID", { system: true, auto: true }),
    column("created_at", "DATETIME", { system: true, auto: true }),
    column("updated_at", "DATETIME", { system: true, auto: true }),
];

interface SampleTable {
    podId: string;
    shape: Table;
    rows: Row[];
    /** Who outside may add rows, when it is open. */
    opening: TableOpening;
}

const day = (offset: number, now: number) => {
    const at = new Date(now + offset * DAY);
    return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
};
const stamp = (offset: number, now: number) => new Date(now + offset * DAY).toISOString();

function table(podId: string, name: string, columns: ColumnSchema[], rows: Row[], now: number, perUser = false): SampleTable {
    const all = [...SYSTEM, ...columns];
    const offered = columns.filter((one) => !one.foreign_key).map((one) => ({
        name: one.name,
        type: one.type,
        required: one.required === true,
        options: one.options ?? [],
        description: one.description ?? null,
        input: one.type === "ENUM" ? "select" : one.type === "BOOLEAN" ? "checkbox" : one.type === "DATE" ? "date" : /email/.test(one.name) ? "email" : one.type === "FLOAT" || one.type === "INTEGER" ? "number" : /details|body|notes/.test(one.name) ? "textarea" : "text",
    }));
    return {
        podId,
        shape: {
            id: `table-${name}`,
            name,
            pod_id: podId,
            primary_key_column: "id",
            enable_rls: perUser,
            columns: all,
            config: null,
            created_at: stamp(-60, now),
            updated_at: stamp(-1, now),
            visibility: "POD",
        },
        rows: rows.map((row, index) => ({ id: `${name}-${index + 1}`, created_at: stamp(-30 + index, now), updated_at: stamp(-index / 4, now), ...row })),
        opening: { table: name, perUser, contactOwned: false, offered, audience: null, columns: [] },
    };
}

function build(now: number): SampleTable[] {
    const customers = table(SUPPORT.id, "customers", [
        column("name", "TEXT", { required: true }),
        column("contact", "TEXT"),
        column("plan", "ENUM", { options: ["Starter", "Team", "Business"] }),
        column("region", "ENUM", { options: ["EU", "US", "APAC"] }),
        column("segment", "ENUM", { options: ["Retail", "Wholesale", "Online"] }),
        column("seats", "INTEGER"),
        column("renews_on", "DATE"),
    ], [
        { name: "Northwind", contact: "Maria Chen", plan: "Business", region: "EU", segment: "Wholesale", seats: 40, renews_on: day(45, now) },
        { name: "Globex", contact: "Omar Haddad", plan: "Team", region: "US", segment: "Retail", seats: 12, renews_on: day(3, now) },
        { name: "Initech", contact: "Li Wei", plan: "Starter", region: "APAC", segment: "Online", seats: 3, renews_on: day(120, now) },
        { name: "Umbrella", contact: "Sam Okafor", plan: "Team", region: "EU", segment: "Online", seats: 9, renews_on: day(70, now) },
        { name: "Hooli", contact: "Ada Novak", plan: "Business", region: "US", segment: "Wholesale", seats: 55, renews_on: day(200, now) },
        { name: "Vandelay Imports", contact: "Lee Park", plan: "Starter", region: "EU", segment: "Retail", seats: 2, renews_on: day(16, now) },
    ], now);
    const customer = (index: number) => idOf(customers.rows[index], "id");

    const tickets = table(SUPPORT.id, "tickets", [
        column("subject", "TEXT", { required: true, description: "What the customer wrote about." }),
        column("status", "ENUM", { options: ["new", "waiting", "solved"], required: true }),
        column("priority", "ENUM", { options: ["low", "normal", "urgent"] }),
        column("channel", "ENUM", { options: ["email", "whatsapp", "chat"] }),
        column("customer_id", "UUID", { foreign_key: { references: "customers.id" } }),
        column("email", "TEXT", { description: "Where to send the answer." }),
    ], [
        { subject: "Order 4471 arrived broken", status: "new", priority: "urgent", channel: "whatsapp", customer_id: customer(0), email: "orders@northwind.example" },
        { subject: "Change the delivery address", status: "new", priority: "normal", channel: "email", customer_id: customer(1), email: "ops@globex.example" },
        { subject: "Invoice shows the wrong VAT", status: "waiting", priority: "normal", channel: "email", customer_id: customer(2), email: "finance@initech.example" },
        { subject: "When does the restock land?", status: "new", priority: "low", channel: "chat", customer_id: customer(3), email: null },
        { subject: "Refund for the cracked lamp", status: "waiting", priority: "urgent", channel: "whatsapp", customer_id: customer(0), email: "maria@northwind.example" },
        { subject: "Add two seats to our plan", status: "solved", priority: "normal", channel: "email", customer_id: customer(4), email: "it@hooli.example" },
        { subject: "Track a parcel to Lyon", status: "solved", priority: "low", channel: "chat", customer_id: customer(5), email: null },
        { subject: "Bulk pricing for 200 shades", status: "new", priority: "normal", channel: "email", customer_id: customer(4), email: "buying@hooli.example" },
        { subject: "Password reset not arriving", status: "solved", priority: "urgent", channel: "email", customer_id: customer(1), email: "omar@globex.example" },
        { subject: "Swap a floor lamp for a desk lamp", status: "waiting", priority: "low", channel: "whatsapp", customer_id: customer(3), email: null },
        { subject: "Gift wrap for a corporate order", status: "new", priority: "low", channel: "chat", customer_id: customer(5), email: null },
    ], now);
    const ticket = (index: number) => idOf(tickets.rows[index], "id");

    const replies = table(SUPPORT.id, "replies", [
        column("subject", "TEXT", { required: true }),
        column("body", "TEXT", { required: true }),
        column("ticket_id", "UUID", { foreign_key: { references: "tickets.id" } }),
        column("tone", "ENUM", { options: ["warm", "plain"] }),
    ], [
        { subject: "Sorry about order 4471", tone: "warm", ticket_id: ticket(0), body: "Hi Maria, I am sorry the lamp arrived cracked. A replacement leaves the warehouse tomorrow morning, and you do not need to send the broken one back. I have also refunded the delivery charge on this order, which you will see on your card in three to five days." },
        { subject: "Your refund is on its way", tone: "warm", ticket_id: ticket(4), body: "The refund for the cracked lamp, $240, went back to the card you paid with today. Banks usually take three to five working days to show it. If it has not arrived by next Friday, reply here and I will chase it with the payment provider for you." },
        { subject: "The VAT line on invoice 1043", tone: "plain", ticket_id: ticket(2), body: "The invoice applied the domestic VAT rate because the billing address on file is in the same country as our warehouse. Once you confirm the registered address of your business, I will reissue the invoice with the reverse charge note and cancel the old one." },
        { subject: "Two more seats for Hooli", tone: "plain", ticket_id: ticket(5), body: "Two seats are added from today, and the next invoice carries them pro rata for the rest of the month. Anyone you invite now can sign in straight away. Seats can be removed again at any time from the billing page, with a credit for unused days." },
    ], now);

    const checklist = table(LAUNCH.id, "launch_checklist", [
        column("task", "TEXT", { required: true }),
        column("owner", "ENUM", { options: ["Ada", "Dev", "Sam"] }),
        column("due", "DATE"),
        column("done", "BOOLEAN", { default: false }),
    ], [
        { task: "Freeze the landing copy", owner: "Ada", due: day(-2, now), done: true },
        { task: "Record the product walkthrough", owner: "Dev", due: day(-1, now), done: false },
        { task: "Send the press list the embargo", owner: "Sam", due: day(0, now), done: false },
        { task: "Check the pricing page in dark", owner: "Dev", due: day(1, now), done: true },
        { task: "Write the launch post", owner: "Ada", due: day(2, now), done: false },
        { task: "Line up three customer quotes", owner: "Sam", due: day(3, now), done: false },
        { task: "Schedule the newsletter", owner: "Ada", due: day(4, now), done: false },
        { task: "Brief support on the new plans", owner: "Sam", due: day(5, now), done: true },
        { task: "Post in the community forum", owner: "Dev", due: day(6, now), done: false },
    ], now);

    const releases = table(LAUNCH.id, "releases", [
        column("title", "TEXT", { required: true }),
        column("shipped_at", "DATE"),
        column("area", "ENUM", { options: ["App", "Web", "API"] }),
        column("version", "TEXT"),
    ], [
        { title: "Shared folders", shipped_at: day(-2, now), area: "App", version: "4.12" },
        { title: "Faster search", shipped_at: day(-6, now), area: "Web", version: "4.11" },
        { title: "Webhooks for orders", shipped_at: day(-9, now), area: "API", version: "4.10" },
        { title: "Dark mode", shipped_at: day(-13, now), area: "App", version: "4.9" },
        { title: "CSV export", shipped_at: day(-20, now), area: "Web", version: "4.8" },
        { title: "Rate limits per key", shipped_at: day(-27, now), area: "API", version: "4.7" },
        { title: "Offline drafts", shipped_at: day(-34, now), area: "App", version: "4.6" },
    ], now);

    const buyers = ["Northwind", "Globex", "Initech", "Umbrella", "Hooli"];
    const invoices = table(BOOKS.id, "invoices", [
        column("number", "TEXT", { required: true }),
        column("customer", "TEXT"),
        column("amount", "FLOAT"),
        column("issued_on", "DATE"),
        column("due_on", "DATE"),
        column("paid", "BOOLEAN", { default: false }),
    ], Array.from({ length: 140 }, (_, index) => ({
        number: `INV-${1200 - index}`,
        customer: buyers[(index * 3) % buyers.length],
        amount: 180 + ((index * 137) % 2400),
        issued_on: day(-index * 2, now),
        due_on: day(30 - index * 2, now),
        paid: index > 6 && index % 5 !== 0,
    })), now);

    /* The books are not everyone's: a table only some people open. */
    invoices.shape.visibility = "RESTRICTED";

    const revenue = table(BOOKS.id, "daily_revenue", [
        column("day", "DATE", { required: true }),
        column("revenue", "FLOAT"),
        column("orders", "INTEGER"),
    ], Array.from({ length: 30 }, (_, index) => {
        const ago = 29 - index;
        const wave = Math.round(Math.sin(index / 3) * 900 + index * 120);
        return { day: day(-ago, now), revenue: 6200 + wave + ((index * 311) % 700), orders: 38 + Math.round(wave / 60) + (index % 4) };
    }), now);

    return [tickets, customers, replies, checklist, releases, invoices, revenue];
}

const WIDGET: WebWidget = {
    id: "widget-website",
    name: "Website",
    publicKey: "pk_sample_support",
    allowedOrigins: [],
    answer: "anyone",
    lookedAfterBy: null,
    embed: '<script src="https://chat.example.com/lemma.js" data-lemma-key="pk_sample_support" async></script>',
    pageUrl: "https://chat.example.com/support",
};

export function tablesSample(): TablesSource {
    let held: SampleTable[] | null = null;
    let made = 0;
    const all = () => (held ??= build(Date.now()));
    const find = (podId: string, name: string) => {
        const found = all().find((one) => one.podId === podId && one.shape.name === name);
        if (!found) throw new Error("There is no table called " + name + " here.");
        return found;
    };
    const chats: Record<string, WebWidget[]> = { [SUPPORT.id]: [WIDGET] };
    const settle = <T,>(value: T) => new Promise<T>((done) => setTimeout(() => done(value), 120));

    return {
        async tables(podId) {
            const mine = all().filter((one) => one.podId === podId);
            return settle({
                items: mine.map((one) => ({ name: one.shape.name, columns: one.shape.columns.length, updatedAt: one.shape.updated_at, perUser: one.shape.enable_rls, access: accessOf(one.shape.visibility, one.shape.enable_rls) })),
                next: null,
            });
        },
        async shape(podId, name) { return settle(find(podId, name).shape); },
        async shapes(podId) { return settle(all().filter((one) => one.podId === podId).map((one) => one.shape)); },
        async rows(podId, name, page) {
            const rows = find(podId, name).rows;
            const from = Number(page ?? 0);
            const next = from + ROWS_PAGE < rows.length ? String(from + ROWS_PAGE) : null;
            return settle({ items: rows.slice(from, from + ROWS_PAGE), next });
        },
        async count(podId, name) { return settle(find(podId, name).rows.length); },
        async record(podId, name, id) {
            const row = find(podId, name).rows.find((one) => idOf(one, "id") === id);
            if (!row) throw new Error("That row is not in this table any more.");
            return settle(row);
        },
        async referencing(podId, name, column, id, limit) {
            return settle(find(podId, name).rows.filter((row) => String(row[column] ?? "") === id).slice(0, limit));
        },
        async linkable(podId, name) { return settle(find(podId, name).rows.slice(0, 200)); },
        async create(podId, name, data) {
            const target = find(podId, name);
            made += 1;
            const at = new Date().toISOString();
            const row: Row = { id: `${name}-new-${made}`, created_at: at, updated_at: at, ...data };
            target.rows = [row, ...target.rows];
            return settle(row);
        },
        async update(podId, name, id, changes) {
            const target = find(podId, name);
            let next: Row | null = null;
            target.rows = target.rows.map((row) => {
                if (idOf(row, "id") !== id) return row;
                next = { ...row, ...changes, updated_at: new Date().toISOString() };
                return next;
            });
            if (!next) throw new Error("That row is not in this table any more.");
            return settle(next);
        },
        async remove(podId, name, id) {
            const target = find(podId, name);
            target.rows = target.rows.filter((row) => idOf(row, "id") !== id);
            await settle(null);
        },
        async opening(podId, name) { return settle(find(podId, name).opening); },
        async openTable(podId, name, audience, columns) {
            const target = find(podId, name);
            target.opening = { ...target.opening, audience, columns };
            return settle(target.opening);
        },
        async closeTable(podId, name) {
            const target = find(podId, name);
            target.opening = { ...target.opening, audience: null, columns: [] };
            await settle(null);
        },
        async webChats(podId) { return settle(chats[podId] ?? []); },
        async makeWebChat(podId, name, answer) {
            const widget: NewWebWidget = { ...WIDGET, id: `widget-${podId}`, name, answer, signingSecret: "sample-secret-not-a-real-key" };
            chats[podId] = [...(chats[podId] ?? []), widget];
            return settle(widget);
        },
    };
}
