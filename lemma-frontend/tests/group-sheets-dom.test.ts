import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement, type FormEvent } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/** Starting a WhatsApp group from inside another form — a bot's channel
 *  settings — must not submit that form.
 *
 *  The sheet is drawn through a portal, and React carries a submit up the
 *  component tree rather than the page, so the start form's submit used to
 *  reach the settings form around it: that form saved and closed, and the
 *  sheet went with it before the invite link could appear. */

/* What the sheet asks of the source: a group WhatsApp has not confirmed,
   then — read again — the same group with its link. */
const started: unknown[] = [];
const group = {
    id: "g-new", surfaceName: "whatsapp", platform: "WHATSAPP", title: "Acme × Northwind", externalId: null,
    inviteLink: null as string | null, pending: true, sharedExternally: false,
    owner: { userId: "sample-user", name: null }, answersOutsiders: true, welcomesOutsiders: true,
    botAnswersOutsiders: true, canManage: true, peopleInSpace: 0, peopleOutside: 0, lastMessageAt: null,
    waitingForYou: 0, updatedAt: new Date().toISOString(),
};
(globalThis as { __groupSheetSource?: unknown }).__groupSheetSource = {
    label: "sample",
    async startGroup(_podId: string, start: unknown) { started.push(start); return group; },
    async getGroup() { return { ...group, pending: false, externalId: "120363@g.us", inviteLink: "https://chat.whatsapp.com/Kx4vQ9", people: [], waiting: [] }; },
    async listGroups() { return []; },
};

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith("/src/data/index.ts")) {
        return { format: "module", shortCircuit: true, source: "export const source = globalThis.__groupSheetSource;" };
    }
    if (url.endsWith("/src/session/use-me.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const useMe = () => "sample-user";' };
    }
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

test("starting a WhatsApp group inside a bot's settings form stays in the sheet through to the link", async () => {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    /* WhatsApp is asked again every two seconds; here, every few
       milliseconds, with timers the test can clear. */
    const timers = new Set<ReturnType<typeof setInterval>>();
    dom.window.setInterval = ((run: () => void) => {
        const timer = setInterval(run, 15);
        timers.add(timer);
        return timer;
    }) as unknown as typeof dom.window.setInterval;
    dom.window.clearInterval = ((timer: ReturnType<typeof setInterval>) => {
        clearInterval(timer);
        timers.delete(timer);
    }) as unknown as typeof dom.window.clearInterval;
    const saved = new Map<string, PropertyDescriptor | undefined>();
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
        IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    /* After the page exists: React DOM decides how to hear typing when it
       loads, and loaded without a document it listens the way old browsers
       needed. */
    const { createRoot } = await import("react-dom/client");
    const { GroupSheets } = await import("../src/space/group-sheets.tsx");
    const container = dom.window.document.getElementById("root")!;
    const root = createRoot(container);
    const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let settingsSaved = 0;
    let closed = 0;
    const pod = { id: "marketing", orgId: "acme", name: "Marketing", iconUrl: null, teammate: { name: "Marketing", initials: "MA", iconUrl: null }, subtitle: "", members: [], waiting: "" };
    const surface = { id: "s-whatsapp", platform: "WHATSAPP", name: "whatsapp", mine: true, agentName: "Marketing", handle: "+1 555-629-5168", active: true };
    const text = () => dom.window.document.body.textContent ?? "";
    try {
        await act(async () => {
            root.render(createElement(QueryClientProvider, { client: cache },
                /* The bot's channel settings: a submit here saves them and
                   closes, as it does in the app. */
                createElement("form", { onSubmit: (event: FormEvent) => { event.preventDefault(); settingsSaved += 1; } },
                    createElement(GroupSheets, {
                        pod: pod as never,
                        sheet: { kind: "whatsapp", surface } as never,
                        onClose: () => { closed += 1; },
                        onOpenGroup: () => undefined,
                    }))));
        });
        const field = dom.window.document.querySelector<HTMLInputElement>(".gsheet input:not([type])");
        assert.ok(field, "the start form asks what the group is for");
        await act(async () => {
            const setValue = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value")!.set!;
            setValue.call(field, "Acme × Northwind");
            field.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
        });
        const form = field.closest("form")!;
        assert.equal(form.parentElement?.closest("form"), null, "the sheet's form is not inside the settings form on the page");
        await act(async () => { form.requestSubmit(); });

        assert.deepEqual(started, [{ surfaceName: "whatsapp", title: "Acme × Northwind", answersOutsiders: true }]);
        assert.equal(settingsSaved, 0, "starting the group must not submit the settings around it");

        /* Pending, then the link: read until it is there. */
        for (let tries = 0; tries < 100 && !text().includes("chat.whatsapp.com/Kx4vQ9"); tries += 1) {
            await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
        }
        assert.match(text(), /Acme × Northwind is ready/);
        assert.match(text(), /chat\.whatsapp\.com\/Kx4vQ9/);
        assert.equal(settingsSaved, 0);
        assert.equal(closed, 0, "the sheet stays open until the person closes it");
    } finally {
        await act(async () => { root.unmount(); });
        for (const timer of timers) clearInterval(timer);
        cache.clear();
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete (globalThis as Record<string, unknown>)[key];
        }
        dom.window.close();
    }
});
