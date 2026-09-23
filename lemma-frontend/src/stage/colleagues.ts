import { displayAgentName, isPodDefaultAgent } from "@/data/agent-names";
import { itemsOf, nameOf } from "@/search/sources";

/** The other agents in a pod, as small cards.
 *
 *  Subordinates rather than peers: the pod's default agent is the one you talk
 *  to, and these are what it delegates to. So they are listed on its profile —
 *  under what it is standing on — rather than given a switcher of their own,
 *  which would suggest choosing between them is a thing a person does.
 */
export interface Colleague {
    /** The row name. The identifier, not the label. */
    name: string;
    label: string;
    blurb: string;
    /** What it is allowed to do, in the words a person would use.
     *
     *  On the wire already — the lean list shape carries `toolsets` — and the
     *  difference between a name with a sentence under it and one that says
     *  "Shell · Browser · Web search" is the difference between a list of
     *  names and knowing which of them to hand something to. */
    can: string[];
}

/** The first sentence of something, for a card that gets one line.
 *
 *  An agent's description can be a paragraph. Cutting mid-sentence reads as a
 *  bug; cutting at the first full stop reads as a summary, which is what the
 *  first sentence of a description usually is.
 */
export function firstSentence(text: string, limit = 120): string {
    const clean = text.replace(/\s+/g, " ").trim();
    if (!clean) return "";
    const stop = clean.search(/[.!?](\s|$)/);
    const sentence = stop === -1 ? clean : clean.slice(0, stop + 1);
    if (sentence.length <= limit) return sentence;
    return sentence.slice(0, limit - 1).trimEnd() + "…";
}

/** Which drawing stands for a capability.
 *
 *  A key rather than the component itself, because this module is pure and is
 *  imported by a test that runs under Node: pulling `@/ui/icons` in here would
 *  drag a `.tsx` file into a runtime with no JSX in it. The mapping from key
 *  to icon lives with the thing that draws it. */
export type ToolsetIcon =
    | "shell" | "browser" | "web" | "pod" | "connectors" | "delegate"
    | "message" | "speech" | "skills" | "memory" | "plan" | "ask"
    | "wait" | "image" | "other";

/** A toolset, as a person would say it.
 *
 *  The codes are the backend's own `AgentToolset`. Named here rather than
 *  shown raw because `WORKSPACE_CLI` and `USER_INTERACTION` are engineering
 *  words for "a shell" and "it can ask you things" — and a card that prints
 *  the enum is a card that has given up.
 *
 *  Two namings, not one, and `word` is the one that gets drawn. One or two
 *  words beside an icon: the icon does the work and the word disambiguates it,
 *  and a strip of eleven reads in a glance. The first attempt put the whole
 *  clause on every chip — "Puts work down and picks it up when the answer
 *  lands" — and eleven of those is a paragraph in pill form that nobody
 *  finishes. `says` survives as the title and the accessible name, where a
 *  longer sentence costs nothing and is there for whoever wants it.
 *
 *  Both live in one table because the two drifting apart is how this app ended
 *  up describing the same twelve toolsets in two vocabularies, one here and
 *  one in `live.ts`. Shortening a word here shortens it on the agent rows too,
 *  which is the point.
 *
 *  Anything this build has not seen is title-cased rather than dropped: a
 *  deployment may ship a toolset newer than this list, and a capability nobody
 *  can read still beats one nobody is told about.
 */
export interface Capability {
    /** The wire code, uppercased. */
    code: string;
    /** One or two words. What gets drawn, everywhere. */
    word: string;
    /** The same fact said properly, for a title and an accessible name. */
    says: string;
    icon: ToolsetIcon;
    /** Whether this says anything about *this* teammate.
     *
     *  `USER_INTERACTION`, `TODO` and `SNOOZE` are in the fixed set the
     *  platform hands every pod's own responder, and they describe how any
     *  agent behaves in a conversation rather than what this one can reach. So
     *  they are true, and uninformative, and drawn last and quietly — rather
     *  than dropped, which would make a section headed "what it can reach for"
     *  into a curated list nobody could check. */
    plain: boolean;
    /** Whether granting this actually hands the agent tools.
     *
     *  `MEMORY` does not, and the backend says so plainly: it "contributes no
     *  tools… it is in this list so Lem is taught the memory contract", and
     *  the reading and writing happen through `WORKSPACE_CLI` and `POD`. It is
     *  a real capability to name and a false one to call a tool, so the flag
     *  is here rather than the distinction being quietly lost. */
    tools: boolean;
}

const TOOLSETS: Record<string, Omit<Capability, "code">> = {
    WORKSPACE_CLI: { word: "Computer", says: "Works a computer", icon: "shell", plain: false, tools: true },
    BROWSER: { word: "Browser", says: "Drives a real browser", icon: "browser", plain: false, tools: true },
    WEB_SEARCH: { word: "Web", says: "Reads the open web", icon: "web", plain: false, tools: true },
    POD: { word: "Files and tables", says: "Works with this teammate’s files and tables", icon: "pod", plain: false, tools: true },
    CONNECTORS: { word: "Connectors", says: "Acts inside this organization's connected accounts", icon: "connectors", plain: false, tools: true },
    SUBAGENTS: { word: "Sub-agents", says: "Hands work to the agents under it", icon: "delegate", plain: false, tools: true },
    MESSAGING: { word: "Messaging", says: "Starts a conversation instead of waiting to be asked", icon: "message", plain: false, tools: true },
    SPEECH: { word: "Voice", says: "Talks, and listens", icon: "speech", plain: false, tools: true },
    SKILLS: { word: "Skills", says: "Uses the skills this teammate has learned", icon: "skills", plain: false, tools: true },
    MEMORY: { word: "Memory", says: "Remembers across conversations", icon: "memory", plain: false, tools: false },
    TODO: { word: "Plans", says: "Writes the work down, then works it", icon: "plan", plain: true, tools: true },
    USER_INTERACTION: { word: "Asks first", says: "Stops to ask a person mid-task", icon: "ask", plain: true, tools: true },
    SNOOZE: { word: "Waits", says: "Puts work down and picks it up when the answer lands", icon: "wait", plain: true, tools: true },
    VIEW_IMAGE: { word: "Images", says: "Looks at a screenshot the way it reads a file", icon: "image", plain: false, tools: true },
};

/** What the pod's default agent runs with, which is not on its row.
 *
 *  `pod_default` comes back over the wire with `"toolsets": []`, and reading
 *  that literally is how this app came to tell people their teammate could
 *  "talk, and that is all" — about an agent with a computer, a browser, the
 *  pod's files and tables, web search, voice, sub-agents and memory.
 *
 *  The list is empty because the default agent's toolset is not stored on it.
 *  `POD_DEFAULT_AGENT_TOOLSETS` in the backend's
 *  `app/modules/agent/tools/registry.py` assigns it at run time: "The pod
 *  default assistant runs with the user's own permissions and gets a fixed,
 *  batteries-included toolset. User-created agents get EXACTLY the toolsets
 *  they were created with — no implicit defaults are added."
 *
 *  **This is a copy of a server-side constant and will not be told when that
 *  constant changes.** Nothing fails loudly if the backend adds a thirteenth;
 *  this page just quietly stops mentioning it. If you are here because the
 *  list looks wrong, that file is the source of truth — read it and copy it
 *  again. The order is the backend's own.
 */
export const POD_DEFAULT_TOOLSETS: readonly string[] = [
    "WORKSPACE_CLI", "BROWSER", "POD", "USER_INTERACTION", "SKILLS", "WEB_SEARCH",
    "SUBAGENTS", "SPEECH", "TODO", "MESSAGING", "SNOOZE", "MEMORY",
];

export function toolsetWord(code: string): string {
    return capabilityFor(code).word;
}

/** One code, resolved. Never throws and never returns nothing for a code that
 *  has characters in it — an unknown toolset is title-cased and drawn with the
 *  neutral mark, because a deployment newer than this build is a normal thing
 *  and a silently missing capability is not. */
export function capabilityFor(code: string): Capability {
    const key = String(code ?? "").trim().toUpperCase();
    const known = TOOLSETS[key];
    if (known) return { code: key, ...known };
    const words = key.toLowerCase().replace(/[_-]+/g, " ").trim();
    const word = words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
    return { code: key, word, says: word, icon: "other", plain: false, tools: true };
}

/** The toolsets an agent actually runs with.
 *
 *  `isDefault` is the whole point: for the pod's own responder an empty array
 *  on the wire means "the fixed set", and for anything else it means empty.
 *  Both are real answers and they are opposites, so the caller has to say
 *  which agent it is asking about. A non-empty list is always taken as it
 *  comes — the backend adds no implicit defaults to an agent somebody made. */
export function grantedToolsets(raw: unknown, isDefault: boolean): string[] {
    const codes = (Array.isArray(raw) ? raw : [])
        .map((one) => (typeof one === "string" ? one.trim().toUpperCase() : ""))
        .filter(Boolean);
    if (codes.length > 0) return [...new Set(codes)];
    return isDefault ? [...POD_DEFAULT_TOOLSETS] : [];
}

/** What an agent can do, ready to draw.
 *
 *  Ordered by how much it explains rather than alphabetically: knowing
 *  something has a shell and a browser tells you what it is for; knowing it
 *  can hold a plan does not. A card has space for a few, and the few should be
 *  the informative ones.
 */
const TELLING_FIRST = [
    "WORKSPACE_CLI", "BROWSER", "WEB_SEARCH", "POD", "CONNECTORS",
    "SUBAGENTS", "MESSAGING", "SPEECH", "SKILLS",
];

export function capabilityList(raw: unknown): Capability[] {
    const codes = (Array.isArray(raw) ? raw : [])
        .map((one) => (typeof one === "string" ? one.trim().toUpperCase() : ""))
        .filter(Boolean);
    const seen = new Set<string>();
    const ranked = [...new Set(codes)].sort((a, b) => {
        const left = TELLING_FIRST.indexOf(a);
        const right = TELLING_FIRST.indexOf(b);
        return (left === -1 ? 99 : left) - (right === -1 ? 99 : right) || a.localeCompare(b);
    });
    const found: Capability[] = [];
    for (const code of ranked) {
        const capability = capabilityFor(code);
        /* Deduped by the words, not the codes: two codes that come out reading
           the same are one thing as far as anybody looking at this is
           concerned, and printing it twice looks like a bug. */
        if (!capability.word || seen.has(capability.word)) continue;
        seen.add(capability.word);
        found.push(capability);
    }
    return found;
}

export function capabilities(raw: unknown): string[] {
    return capabilityList(raw).map((capability) => capability.word);
}

/** Everyone but the one you are already talking to.
 *
 *  The default agent is excluded because the whole page is already about it —
 *  listing it among its own subordinates is the page introducing itself twice.
 */
export function colleaguesFrom(raw: unknown): Colleague[] {
    return itemsOf(raw)
        .filter((item) => !isPodDefaultAgent(
            typeof item.name === "string" ? item.name : null,
            typeof item.kind === "string" ? item.kind : null,
        ))
        .map((item) => {
            const name = nameOf(item, "agent");
            return {
                name,
                label: displayAgentName(name, typeof item.kind === "string" ? item.kind : null),
                blurb: firstSentence(
                    typeof item.description === "string" ? item.description
                    : typeof item.instructions === "string" ? item.instructions
                    : "",
                ),
                can: capabilities(item.toolsets),
            };
        })
        .sort((a, b) => a.label.localeCompare(b.label));
}
