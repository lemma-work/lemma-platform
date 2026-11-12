import { SAMPLE_LEMMAS } from "../../../data/sample";
import { SAMPLE_ACCESS } from "../people/sample";
import type { LibraryItem, PodLink } from "./model";
import type { WorksWithSource } from "./source";

const DAY = 86_400_000;

function ago(ms: number): string {
    return new Date(Date.now() - ms).toISOString();
}

function lemmaOf(podId: string) {
    return SAMPLE_LEMMAS.find((one) => one.id === podId) ?? null;
}

function link(podId: string, shared: PodLink["shared"], stewardName: string): PodLink {
    const lemma = lemmaOf(podId);
    return { podId, name: lemma?.name ?? "A Lemma", iconUrl: lemma?.iconUrl ?? null, ...(lemma?.description ? { description: lemma.description } : {}), stewardName, shared };
}

/** What every sample Lemma has to share: three tables, one of them Public,
 *  and a few folders, one with folders of its own, beside the person's own
 *  `/me`, which is never offered. */
const TABLES: LibraryItem[] = [
    { id: "t-tickets", name: "tickets", kind: "table", path: "tickets", updated: ago(2 * DAY), detail: "Table", visibility: "POD" },
    { id: "t-customers", name: "customers", kind: "table", path: "customers", updated: ago(5 * DAY), detail: "Table", visibility: "POD" },
    { id: "t-prices", name: "prices", kind: "table", path: "prices", updated: ago(9 * DAY), detail: "Table", visibility: "PUBLIC" },
];

const FOLDERS: Record<string, LibraryItem[]> = {
    "/": [
        { id: "f-faq", name: "faq", kind: "folder", path: "/faq", updated: ago(3 * DAY), detail: "" },
        { id: "f-policies", name: "policies", kind: "folder", path: "/policies", updated: ago(6 * DAY), detail: "" },
        { id: "f-reports", name: "reports", kind: "folder", path: "/reports", updated: ago(1 * DAY), detail: "", visibility: "PUBLIC" },
        { id: "f-me", name: "me", kind: "folder", path: "/me", updated: ago(1 * DAY), detail: "" },
        { id: "f-welcome", name: "Welcome.md", kind: "file", path: "/Welcome.md", updated: ago(8 * DAY), detail: "" },
    ],
    "/policies": [
        { id: "f-refunds", name: "refunds", kind: "folder", path: "/policies/refunds", updated: ago(6 * DAY), detail: "" },
        { id: "f-shipping", name: "shipping", kind: "folder", path: "/policies/shipping", updated: ago(12 * DAY), detail: "" },
    ],
};

/** Who may ask whom, in memory while the app is open. Launch may ask
 *  Support, reading its tickets and FAQ; Books lets Support ask it; Hiring,
 *  which Ada is not in, may ask Renewals. Everything else Ada can ask as
 *  herself, since she is in both. */
export function worksWithSample(): WorksWithSource {
    const links = new Map<string, PodLink[]>([
        ["sample-support-1140", [link("sample-launch-1440", [{ kind: "table", name: "tickets" }, { kind: "folder", name: "/faq" }], "Sam Okafor")]],
        ["sample-books-986", [link("sample-support-1140", [{ kind: "table", name: "invoices" }], "Ada Lovelace")]],
        ["sample-renewals-790", [link("sample-hiring-1078", [], "Sam Okafor")]],
    ]);
    const inIt = (podId: string) => (SAMPLE_ACCESS[podId] ?? []).some((one) => one.userId === "me");

    return {
        askable: async (podId) => SAMPLE_LEMMAS
            .filter((other) => other.id !== podId)
            .map((other) => ({
                id: other.id,
                name: other.name,
                iconUrl: other.iconUrl,
                ...(other.description ? { description: other.description } : {}),
                throughYou: inIt(podId) && inIt(other.id),
                connected: (links.get(other.id) ?? []).some((one) => one.podId === podId),
            }))
            .filter((other) => other.throughYou || other.connected),
        links: async (podId) => links.get(podId) ?? [],
        connect: async (podId, askingPodId, shares) => {
            if (!inIt(askingPodId)) throw Object.assign(new Error("You can only connect a Lemma you are in."), { statusCode: 409 });
            if (shares.some((one) => one.name === "/me" || one.name.startsWith("/me/"))) throw Object.assign(new Error("That cannot be shared."), { statusCode: 400 });
            const held = (links.get(podId) ?? []).filter((one) => one.podId !== askingPodId);
            const before = (links.get(podId) ?? []).find((one) => one.podId === askingPodId);
            links.set(podId, [...held, link(askingPodId, [...shares], before?.stewardName ?? "Ada Lovelace")]);
        },
        disconnect: async (podId, askingPodId) => {
            links.set(podId, (links.get(podId) ?? []).filter((one) => one.podId !== askingPodId));
        },
        shareable: async (_podId, kind, directory) => ({ items: kind === "tables" ? TABLES : FOLDERS[directory] ?? [], next: null }),
    };
}
