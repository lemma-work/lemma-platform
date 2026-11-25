/** What the Models screens decide, away from the screens: how a key, a
 *  coding agent and a computer read on one dense row, what the organization
 *  and a Lemma run on, and how the picker's rows are made. Pure, so `tests/`
 *  can hold it. The readers and rules are the web's (`web/runtimes.ts`,
 *  `web/provider-draft.ts`, `web/runs-on-state.ts`); the parts of
 *  `org/models.tsx` and `shell/runs-on.tsx` that decide things are ported
 *  here, said for a phone. */

import { canBeOrganizationDefault } from "@web/org/provider-draft";
import {
    agentDefaultLabel, chosenModel, describeChoice, offeredSelections, optionsFor, sameChoice, shortModel, stillLooking,
    type AgentOption, type Choice, type Computer, type LocalAgent, type Runtime, type RuntimeModel,
} from "@web/data/runtimes";

/* ── the organization's list ───────────────────────────────────────── */

/** Prefilled routes for the providers people connect most (the web's
 *  `PRESETS`). Everything else is one of the two protocols with another
 *  address, typed into the address field. */
export const PRESETS: readonly { id: string; protocol: "openai" | "anthropic"; name: string; baseUrl: string }[] = [
    { id: "openrouter", protocol: "openai", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1" },
    { id: "openai", protocol: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1" },
    { id: "anthropic", protocol: "anthropic", name: "Anthropic", baseUrl: "https://api.anthropic.com" },
    { id: "groq", protocol: "openai", name: "Groq", baseUrl: "https://api.groq.com/openai/v1" },
    { id: "deepseek", protocol: "openai", name: "DeepSeek", baseUrl: "https://api.deepseek.com" },
    { id: "xai", protocol: "openai", name: "xAI", baseUrl: "https://api.x.ai/v1" },
    { id: "together", protocol: "openai", name: "Together", baseUrl: "https://api.together.xyz/v1" },
    { id: "mistral", protocol: "openai", name: "Mistral", baseUrl: "https://api.mistral.ai/v1" },
];

/** How often the computers are read again while the screen is open (the
 *  web's `computerPoll`): quickly only while a fresh pairing is still being
 *  looked for, since that is somebody watching for it. */
export function computerPoll(computers: readonly Computer[] | undefined, now = Date.now()): number {
    if (!computers) return 4_000;
    if (computers.some((one) => stillLooking(one, now))) return 4_000;
    const unsettled = computers.length === 0 || computers.some((one) => !one.online);
    return unsettled ? 30_000 : 20_000;
}

export function modelCount(count: number): string {
    return count ? count + (count === 1 ? " model" : " models") : "";
}

/** The organization's list, cut the way the screen draws it: what each
 *  computer's agents were added as, the keys (and any coding agent whose
 *  computer is not listed here), and the counts for the words on top. */
export function ledger(runtimes: readonly Runtime[], computers: readonly Computer[]) {
    const savedByAgent = new Map<string, Runtime>();
    for (const runtime of runtimes) if (runtime.harnessId) savedByAgent.set(runtime.harnessId, runtime);
    const known = new Set(computers.flatMap((one) => one.agents.map((agent) => agent.id)));
    const loose = runtimes.filter((runtime) => !runtime.harnessId || !known.has(runtime.harnessId));
    return {
        savedByAgent,
        keys: loose.filter((runtime) => runtime.kind === "key"),
        /** Added coding agents whose computer is not in this list: another
         *  person's, shared, or one whose computer was removed. */
        elsewhere: loose.filter((runtime) => runtime.kind === "agent"),
        retired: loose.filter((runtime) => runtime.archived).length,
        available: runtimes.filter((runtime) => !runtime.archived && !runtime.trouble).length,
        troubled: runtimes.filter((runtime) => !runtime.archived && runtime.trouble).length,
    };
}

/** "3 to pick from · 1 needing attention", "Nothing to pick from". */
export function countWords(available: number, troubled: number): string {
    return (available || "Nothing") + " to pick from" + (troubled ? " · " + troubled + " needing attention" : "");
}

export type Tone = "ok" | "warn" | "muted";

/** What kind of thing a runtime is, in a word or two. */
export function kindOf(runtime: Runtime): string {
    if (runtime.scope === "system") return "Built in";
    if (runtime.kind === "key") return runtime.scope === "personal" ? "Your key" : "Shared key";
    return runtime.scope === "personal" ? "Yours" : "Shared";
}

/** A key's row: what kind it is and how many models, and its state. Only
 *  the exception is marked: the default, and a key that is yours alone. */
export function keyRow(runtime: Runtime, isDefault: boolean): { hint: string; value: string; tone: Tone } {
    const hint = [kindOf(runtime), modelCount(runtime.models.length)].filter(Boolean).join(" · ");
    if (runtime.archived) return { hint, value: "Retired", tone: "muted" };
    if (runtime.trouble) return { hint, value: runtime.trouble, tone: "warn" };
    return { hint, value: isDefault ? "Default" : "Available", tone: "ok" };
}

/** Whether a key can be tested from here: the organization's own, not
 *  built in, not retired. */
export function testable(runtime: Runtime): boolean {
    return runtime.kind === "key" && runtime.scope !== "system" && !runtime.archived;
}

/** What a Test found, said once: the backend's own sentence, and how many
 *  models it lists when it answered. */
export function testWords(found: { ok: boolean; message: string; models: string[] | null }): string {
    return found.ok && found.models ? found.message + " It lists " + modelCount(found.models.length) + "." : found.message;
}

/** The keys that can be everyone's default: the organization's own, live,
 *  providers rather than coding agents (the backend's rule). */
export function defaultChoices(runtimes: readonly Runtime[]): { value: string; label: string; hint: string }[] {
    return runtimes.filter(canBeOrganizationDefault).map((runtime) => ({
        value: runtime.id,
        label: runtime.name,
        hint: runtime.defaultModel ? "Uses " + labelOf(runtime, runtime.defaultModel) + " unless told otherwise" : modelCount(runtime.models.length),
    }));
}

function labelOf(runtime: Runtime, model: string): string {
    return runtime.models.find((entry) => entry.name === model)?.label ?? shortModel(model);
}

/** A coding agent on a computer, on one row: its name, what else it is
 *  called and how many models, and what it can do now. */
export function agentRow(agent: LocalAgent, computer: Computer, saved: Runtime | null): { name: string; hint: string; value: string; tone: Tone; added: boolean } {
    const added = Boolean(saved) && !saved!.archived;
    const usable = agent.ready && computer.online;
    const hint = [
        saved && saved.name !== agent.name ? agent.name : null,
        saved?.scope === "personal" ? "Yours" : saved?.scope === "org" ? "Shared" : null,
        modelCount(agent.models.length),
    ].filter(Boolean).join(" · ");
    const value = saved?.archived ? "Retired"
        : !computer.online ? "Computer offline"
            : !usable ? agent.state
                : added ? "Available" : "Not added yet";
    const tone: Tone = saved?.archived || !computer.online ? "muted"
        : !usable ? (agent.state === "Setting up" ? "muted" : "warn")
            : added ? "ok" : "muted";
    return { name: saved?.name ?? agent.name, hint, value, tone, added };
}

/** What an added coding agent is set to: its model, then each option it
 *  publishes, as chosen or as that computer has it. Without the live agent
 *  (its computer is not listed here) only what was saved can be said. */
export function agentSetTo(agent: LocalAgent | null, saved: Runtime): { label: string; value: string }[] {
    if (!agent) {
        const rows = [{ label: "Model", value: saved.defaultModel ? labelOf(saved, saved.defaultModel) : agentDefaultLabel() }];
        for (const [key, value] of Object.entries(saved.selections)) rows.push({ label: words(key), value });
        return rows;
    }
    const model = agent.models.some((entry) => entry.name === saved.defaultModel) ? saved.defaultModel : "";
    const rows = [{
        label: "Model",
        value: model ? agent.models.find((entry) => entry.name === model)?.label ?? shortModel(model) : agentDefaultLabel(agent.defaultModel),
    }];
    const options = optionsFor({ options: agent.options, byModel: agent.optionsByModel }, model);
    for (const option of options) {
        const picked = option.choices.find((choice) => choice.value === saved.selections[option.key]);
        const now = option.choices.find((choice) => choice.value === option.current)?.label;
        rows.push({ label: option.label, value: picked ? picked.label : "As set there" + (now ? " (" + now + ")" : "") });
    }
    return rows;
}

function words(key: string): string {
    const said = key.replaceAll("_", " ").toLowerCase();
    return said.charAt(0).toUpperCase() + said.slice(1);
}

/** Who can pick an added agent. */
export function whoPicks(saved: Runtime, orgName: string): string {
    return saved.scope === "personal" ? "Only you" : "Everyone in " + orgName;
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** A computer's quieter words: its Lemma app's version, and when an
 *  offline one was last seen ("last seen 19:12" today, else the day). */
export function computerWords(computer: Computer, now = new Date()): string {
    const parts = [computer.release ? "Lemma app " + computer.release : ""];
    if (!computer.online && computer.lastSeen) {
        const at = new Date(computer.lastSeen);
        if (!Number.isNaN(at.getTime())) {
            const today = at.toDateString() === now.toDateString();
            parts.push("last seen " + (today
                ? String(at.getHours()).padStart(2, "0") + ":" + String(at.getMinutes()).padStart(2, "0")
                : at.getDate() + " " + MONTHS[at.getMonth()]));
        }
    }
    return parts.filter(Boolean).join(" · ");
}

/* ── what a Lemma runs on ──────────────────────────────────────────── */

/** A row of the picker: a runtime, one of its models, and what to call it. */
export interface PickRow {
    runtime: Runtime;
    choice: Choice;
    label: string;
}

/** Every model, flattened into the rows a person picks from (the web's
 *  `rowsOf`). A runtime naming no model still gets one row, since it runs
 *  something; an unpinned coding agent gets "Agent default" first, since
 *  showing only its models made the first look chosen when nothing was. */
export function pickRows(runtimes: readonly Runtime[]): PickRow[] {
    return runtimes.flatMap((runtime) => {
        const own = { runtime, choice: { runtimeId: runtime.id, model: "" }, label: "" };
        if (runtime.models.length === 0) return [{ ...own, label: "Its usual model" }];
        const models = runtime.models.map((model: RuntimeModel) => ({
            runtime,
            choice: { runtimeId: runtime.id, model: model.name },
            label: model.label,
        }));
        return runtime.kind === "agent" && !runtime.defaultModel
            ? [{ ...own, label: agentDefaultLabel() }, ...models]
            : models;
    });
}

/** Whether a model's label and its id are one name in other punctuation, so
 *  the id would only say it twice. */
export function sameName(label: string, model: string): boolean {
    const plain = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, "");
    return plain(label) === plain(shortModel(model));
}

/** Whether a row is the one chosen: an open model and the model it
 *  resolves to are the same pick. */
export function isPicked(runtime: Runtime, choice: Choice | null, row: Choice): boolean {
    return Boolean(choice) && choice!.runtimeId === runtime.id
        && (sameChoice(choice, row) || chosenModel(runtime, choice) === row.model);
}

/** What a newly picked row keeps of the choice before it: only the same
 *  agent's selections the new model still offers (the web's `carried`). */
export function carried(next: Choice, before: Choice | null, runtime: Runtime | undefined, sets: Runtime["options"]): Choice {
    if (!before?.selections || before.runtimeId !== next.runtimeId || runtime?.id !== next.runtimeId || !sets) return next;
    const kept = offeredSelections(optionsFor(sets, chosenModel(runtime, next)), before.selections);
    return Object.keys(kept).length ? { ...next, selections: kept } : next;
}

/** A choice with one option set, or cleared back to the agent's own. */
export function withSelection(choice: Choice, key: string, value: string): Choice {
    const next = { ...(choice.selections ?? {}) };
    if (value) next[key] = value;
    else delete next[key];
    return Object.keys(next).length ? { ...choice, selections: next } : { runtimeId: choice.runtimeId, model: choice.model };
}

/** The options a chosen coding agent offers for the model it runs. */
export function chosenOptions(runtime: Runtime | undefined, choice: Choice | null, sets: Runtime["options"]): AgentOption[] {
    if (!runtime || runtime.kind !== "agent" || !choice || choice.runtimeId !== runtime.id || !sets) return [];
    return optionsFor(sets, chosenModel(runtime, choice));
}

/** How many of a runtime's rows a phone shows before "Show all". */
export const ROWS_SHOWN = 12;

/** A long list cut to its first rows, keeping the chosen one in view. */
export function shownRows(rows: readonly PickRow[], all: boolean, picked: (row: PickRow) => boolean): { rows: PickRow[]; hidden: number } {
    if (all || rows.length <= ROWS_SHOWN) return { rows: [...rows], hidden: 0 };
    const head = rows.slice(0, ROWS_SHOWN);
    const chosen = rows.slice(ROWS_SHOWN).find(picked);
    return { rows: chosen ? [...head, chosen] : head, hidden: rows.length - head.length - (chosen ? 1 : 0) };
}

/** What a Lemma runs on, in one phrase: its own pick, the organization's
 *  default it follows, or that there is nothing to run on. `setup` is the
 *  web's `modelSetupState`. */
export function lemmaModelSaid(live: readonly Runtime[], choice: Choice | null, inherited: Choice | null, setup: "none" | "default-missing" | null): string {
    if (choice) return describeChoice([...live], choice) || "What it was set to is gone";
    if (setup) return "No model yet: pick one";
    const inheritedName = describeChoice([...live], inherited);
    return inheritedName ? "Organization default: " + inheritedName : "Organization default";
}
