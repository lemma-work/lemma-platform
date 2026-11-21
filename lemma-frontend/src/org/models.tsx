import { LoadingIndicator } from "@/ui/loading";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    source,
    agentFix,
    agentLogo,
    agentSettingsChanges,
    stillLooking,
    saidAbout,
    type AgentSettings,
    type Computer,
    type LocalAgent,
    type Runtime,
    type RuntimeTest,
} from "@/data";
import { downloadUrl } from "@/session/client";
import { Modal } from "@/shell/modal";
import { useIsDesktop } from "@/desktop/bridge";
import { CheckAgainButton, OwnSettingsSwitch, ThisComputerCard, useThisHostId } from "@/desktop/this-computer-card";
import { useThisComputer } from "@/desktop/this-computer";
import { ThisMacModelSuggestions } from "@/desktop/this-mac-models";
import { LOCAL_SERVERS, LOCAL_SERVER_KEY, detectLocalServers, friendlyError, thisMac } from "@/desktop/this-mac";
import { useThisMacAvailability } from "@/desktop/this-mac-settings";
import {
    asksAboutImages,
    canBeOrganizationDefault,
    chosenVisionModels,
    discoveryRequest,
    firstProviderOffer,
    isLocalRoute,
    keyToSend,
    localRouteAnswering,
    modelNames,
    readDiscoveredModels,
    testFailureMessage,
    visionCandidates,
} from "./provider-draft";
import { AgentSettingsFields, EditAgentSettings } from "./agent-settings";
import {
    DownloadIcon,
    KeyIcon,
    PlusIcon,
    RefreshIcon,
    LemmaMark,
    TerminalIcon,
} from "@/ui/icons";
import { Problem } from "@/outcomes/calm";
import { MoreSettings, NOT_A_LOGIN, SecretInput } from "@/ui/form";

/** Everything a teammate in this organization can be run on.
 *
 *  One ledger, not two lists that look the same. A bought API key and Claude
 *  Code on a laptop are the same object to whoever picks one — same id, same
 *  slot in a conversation — and drawing them as peer sections meant the same
 *  agent appeared twice, under the same name, both saying "Ready". So a
 *  computer is a heading *inside* the list, and every model is written down
 *  exactly once, in the place it comes from.
 *
 *  What a browser cannot do is pair a machine. Agent Host ships in the Lemma
 *  desktop app and is supervised by it; a browser has nothing to pair and
 *  handing out a pairing code would hand out a credential nothing can spend.
 *  So in a browser the computers here are the ones that app already
 *  connected, and the empty state asks for the app. Inside the app, this
 *  computer connects itself and heads the list with its live status. */

/* Prefilled routes for the providers people actually connect. Everything else
   is the same two protocols with a different URL, which is what "Something
   else" is for. */
const PRESETS: { id: string; protocol: "openai" | "anthropic"; name: string; baseUrl: string }[] = [
    { id: "openrouter", protocol: "openai", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1" },
    { id: "openai", protocol: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1" },
    { id: "anthropic", protocol: "anthropic", name: "Anthropic", baseUrl: "https://api.anthropic.com" },
    { id: "groq", protocol: "openai", name: "Groq", baseUrl: "https://api.groq.com/openai/v1" },
    { id: "deepseek", protocol: "openai", name: "DeepSeek", baseUrl: "https://api.deepseek.com" },
    { id: "xai", protocol: "openai", name: "xAI", baseUrl: "https://api.x.ai/v1" },
    { id: "together", protocol: "openai", name: "Together", baseUrl: "https://api.together.xyz/v1" },
    { id: "mistral", protocol: "openai", name: "Mistral", baseUrl: "https://api.mistral.ai/v1" },
];

/* Offered only inside the Lemma app on the machine it runs on: "this
   computer" means the one the backend can reach on loopback, which in a
   browser against a shared server is somebody else's machine. */
const OLLAMA_PRESET: typeof PRESETS[number] = {
    id: "ollama-local",
    protocol: "openai",
    name: "Ollama (this computer)",
    baseUrl: LOCAL_SERVERS.find((server) => server.id === "ollama")?.baseUrl ?? "",
};

/** A computer that has just been paired publishes its agents a few seconds
 *  later, and one that is waking up changes status on its own. Poll quickly
 *  only while a fresh pairing is still being looked for — that is a person
 *  watching the screen for it. No computer at all, or one offline, can stay
 *  that way for days, and every poll here lists each computer's harnesses
 *  too, so those are looked at twice a minute rather than every four seconds:
 *  still soon enough that a machine does not sit at "Offline" until the
 *  window is refocused. */
function computerPoll(computers: Computer[] | undefined): number {
    if (!computers) return 4_000;
    if (computers.some((one) => stillLooking(one))) return 4_000;
    const unsettled = computers.length === 0 || computers.some((one) => !one.online);
    return unsettled ? 30_000 : 20_000;
}

/** What a row's thing is, in the kit's circle: its maker's logo where it
 *  has one, Lemma's mark for the built-in models, else a terminal or a key. */
function Mark({ runtime }: { runtime: { harness: string; kind: "key" | "agent"; scope?: string } }) {
    const logo = agentLogo(runtime.harness);
    if (logo) return <span className="kmark" aria-hidden="true"><img src={logo} alt="" /></span>;
    if (runtime.scope === "system") return <span className="kmark" aria-hidden="true"><LemmaMark size={16} /></span>;
    if (runtime.kind === "agent") return <span className="kmark" aria-hidden="true"><TerminalIcon /></span>;
    return <span className="kmark" aria-hidden="true"><KeyIcon /></span>;
}

/** A tone, as the kit's status: a dot and a word. */
function statusClass(tone: "ok" | "warn" | "muted"): string {
    return tone === "ok" ? "kstatus" : tone === "warn" ? "kstatus kstatus--warn" : "kstatus kstatus--off";
}

/** One row, whatever fills it.
 *
 *  A bought key, a coding agent on a laptop and an agent nobody has added yet
 *  are one row on purpose: the question the reader is asking is the same for
 *  all three — can a conversation pick this now — so they answer it in the
 *  same place, in the same words: what it is at the left, what it says in
 *  the middle, what can be done and how it stands at the right. */
function Row({
    mark,
    name,
    detail,
    tag,
    note,
    state,
    tone,
    action,
    quiet,
    result,
}: {
    mark: React.ReactNode;
    name: string;
    detail?: string;
    tag?: string;
    note?: React.ReactNode;
    state: string;
    tone: "ok" | "warn" | "muted";
    action?: React.ReactNode;
    quiet?: boolean;
    /** What the last thing asked of this row found — a Test, or a failed
     *  "Make default" — said under it until the row is asked again. */
    result?: { ok: boolean; message: string } | null;
}) {
    return (
        <li className={"krow" + (quiet ? " kset-quiet" : "")}>
            <span className="krow__lead">
                {mark}
                <span className="krow__v">
                    <b>{name}</b>
                    {detail && <span>{detail}</span>}
                    {note && <span className="kset-under kset-long">{note}</span>}
                    {result && (
                        <span role="status" className={"kset-long" + (result.ok ? "" : " kset-said--bad")}>
                            {result.message}
                        </span>
                    )}
                </span>
            </span>
            <span className="krow__end">
                {tag && <span className="ktag">{tag}</span>}
                {action}
                <span className={statusClass(tone)}>{state}</span>
            </span>
        </li>
    );
}

/** A fix names the command to type between backticks; it is drawn as code,
 *  so it reads as something to type rather than as punctuation. */
function withCode(text: string): React.ReactNode {
    const parts = text.split("`");
    if (parts.length < 3) return text;
    return parts.map((part, index) => (index % 2 === 1 ? <code key={index}>{part}</code> : part));
}

function modelCount(count: number): string {
    return count ? count + (count === 1 ? " model" : " models") : "";
}

/** A saved runtime with no live agent behind it: a provider key, or a coding
 *  agent whose computer is not in this list. */
function RuntimeRow({
    runtime,
    orgId,
    onChanged,
    answering = null,
    isDefault = false,
}: {
    runtime: Runtime;
    orgId: string;
    onChanged: () => void;
    /** For a model server on this computer: whether it answered just now.
     *  `null` when that is not a question this row can ask. */
    answering?: boolean | null;
    /** Whether this is what every teammate that names no model runs on. */
    isDefault?: boolean;
}) {
    const [confirming, setConfirming] = useState(false);
    const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
    const check = useMutation({
        mutationFn: () => source.testRuntime(orgId, runtime.id),
        onMutate: () => setResult(null),
        onSuccess: (found: RuntimeTest) => setResult({
            ok: found.ok,
            message: found.ok && found.models
                ? found.message + " It lists " + modelCount(found.models.length) + "."
                : found.message,
        }),
        onError: (problem) => setResult({ ok: false, message: saidAbout(problem, "The test could not be run.") }),
    });
    /* Following the key's own default model, not pinning today's: when the
       provider renames or drops it, teammates move with the key. */
    const makeDefault = useMutation({
        mutationFn: () => source.setOrganizationDefault(orgId, { runtimeId: runtime.id, model: "" }),
        onMutate: () => setResult(null),
        onSuccess: onChanged,
        onError: (problem) => setResult({ ok: false, message: saidAbout(problem, "That could not be made the default.") }),
    });
    const testable = runtime.kind === "key" && runtime.scope !== "system" && !runtime.archived;
    const canBeDefault = canBeOrganizationDefault(runtime) && !isDefault;
    const archive = useMutation({
        mutationFn: () => source.archiveRuntime(orgId, runtime.id),
        onSuccess: () => { setConfirming(false); onChanged(); },
    });
    const restore = useMutation({
        mutationFn: () => source.restoreRuntime(orgId, runtime.id),
        onSuccess: onChanged,
    });

    const detail = [
        runtime.scope === "system" ? "Built in" : runtime.kind === "key" ? "Shared key" : null,
        modelCount(runtime.models.length),
    ].filter(Boolean).join(" · ");

    return (
        <Row
            mark={<Mark runtime={runtime} />}
            name={runtime.name}
            detail={detail}
            /* Only the exception is marked. Organization scope is where
               almost everything lands, so labelling it would put an
               identical chip on every row and crowd out the one that says
               something: this one is yours alone. */
            tag={isDefault ? "Default" : runtime.scope === "personal" ? "Yours" : undefined}
            note={!runtime.archived && answering === false
                ? "Nothing is answering at this address. Start the model server on this computer, then check again."
                : undefined}
            state={runtime.archived ? "Retired" : runtime.trouble || (answering === false ? "Not answering" : "Available")}
            tone={runtime.archived ? "muted" : runtime.trouble || answering === false ? "warn" : "ok"}
            quiet={runtime.archived}
            result={result}
            action={
                runtime.scope === "system" ? undefined : runtime.archived ? (
                    <button type="button" className="b b--s b--secondary" disabled={restore.isPending} onClick={() => restore.mutate()}>
                        {restore.isPending ? "Bringing back…" : "Bring back"}
                    </button>
                ) : confirming ? (
                    /* Red ink only once it has been asked for: a retired
                       key can be brought back, so this is not the danger
                       button. */
                    <>
                        <button type="button" className="b b--s b--secondary b--red" disabled={archive.isPending} onClick={() => archive.mutate()}>
                            {archive.isPending ? "Retiring…" : "Retire"}
                        </button>
                        <button type="button" className="b b--s b--quiet" onClick={() => setConfirming(false)}>Keep</button>
                    </>
                ) : (
                    <>
                        {testable && (
                            <button type="button" className="b b--s b--quiet" disabled={check.isPending} onClick={() => check.mutate()}>
                                {check.isPending ? "Testing…" : "Test"}
                            </button>
                        )}
                        {canBeDefault && (
                            <button type="button" className="b b--s b--quiet" disabled={makeDefault.isPending} onClick={() => makeDefault.mutate()}>
                                {makeDefault.isPending ? "Saving…" : "Make default"}
                            </button>
                        )}
                        <button type="button" className="b b--s b--quiet" onClick={() => setConfirming(true)}>Retire</button>
                    </>
                )
            }
        />
    );
}

/** One coding agent on one computer — and, if somebody added it, the runtime
 *  it is pickable as. One row, because they are one thing: whether a
 *  conversation can use this agent is a fact about the agent, not a second
 *  object living in a list above. */
function AgentRow({
    agent,
    computer,
    here,
    saved,
    orgId,
    onChanged,
}: {
    agent: LocalAgent;
    computer: Computer;
    /** What to call the computer when it is the one this app runs on — "this
     *  Mac" — and null for any other. */
    here: string | null;
    saved: Runtime | null;
    orgId: string;
    onChanged: () => void;
}) {
    const [adding, setAdding] = useState(false);
    const [editing, setEditing] = useState(false);
    const restore = useMutation({
        mutationFn: () => source.restoreRuntime(orgId, saved!.id),
        onSuccess: onChanged,
    });

    const added = Boolean(saved) && !saved!.archived;
    const usable = agent.ready && computer.online;
    const name = saved?.name ?? agent.name;

    const state = saved?.archived
        ? "Retired"
        : !computer.online
            ? "Computer offline"
            : !usable
                ? agent.state
                : added
                    ? "Available"
                    : "Not added yet";
    const tone: "ok" | "warn" | "muted" = saved?.archived || !computer.online
        ? "muted"
        : !usable
            ? (agent.state === "Setting up" ? "muted" : "warn")
            : added
                ? "ok"
                : "muted";

    return (
        <>
            <Row
                mark={<Mark runtime={{ harness: agent.harness, kind: "agent" }} />}
                name={name}
                detail={[
                    saved && saved.name !== agent.name ? agent.name : null,
                    modelCount(agent.models.length),
                ].filter(Boolean).join(" · ")}
                tag={saved?.scope === "personal" ? "Yours" : saved?.scope === "org" ? "Shared" : undefined}
                /* Said only when the computer itself is reachable. When it is
                   not, its own heading already said so, and repeating it under
                   every agent is the same sentence three times. */
                note={computer.online && !agent.ready
                    ? withCode(agentFix(agent, here))
                    /* The switch acts on this computer's Agent Host, so it is
                       drawn only beside this computer's own agents -- here as
                       well as under This Mac, where people add and manage
                       them. */
                    : here
                        ? <OwnSettingsSwitch harness={agent.harness} name={agent.name} />
                        : undefined}
                state={state}
                tone={tone}
                quiet={!computer.online}
                action={
                    saved?.archived ? (
                        <button type="button" className="b b--s b--secondary" disabled={restore.isPending} onClick={() => restore.mutate()}>
                            {restore.isPending ? "Bringing back…" : "Bring back"}
                        </button>
                    ) : added && usable ? (
                        <button type="button" className="b b--s b--quiet" onClick={() => setEditing(true)}>Settings</button>
                    ) : added || !usable ? undefined : (
                        /* Offered only while that computer can actually take
                           it. Adding binds the runtime to the live agent — the
                           backend asks the machine what it offers — so a
                           sleeping laptop would mean filling in a dialog and
                           then failing on save. */
                        <button type="button" className="b b--s b--secondary" onClick={() => setAdding(true)}>
                            <PlusIcon /> Add
                        </button>
                    )
                }
            />
            {adding && (
                <AddAgent agent={agent} computer={computer} orgId={orgId} onClose={() => setAdding(false)} onAdded={onChanged} />
            )}
            {editing && saved && (
                <EditAgentSettings
                    agent={agent}
                    computer={computer}
                    runtime={saved}
                    orgId={orgId}
                    onClose={() => setEditing(false)}
                    onSaved={onChanged}
                />
            )}
        </>
    );
}

/** Adding a coding agent a computer found, so Lemmas can pick it: its name,
 *  its model and options, and whether anyone else here may use it. */
function AddAgent({
    agent,
    computer,
    orgId,
    onClose,
    onAdded,
}: {
    agent: LocalAgent;
    computer: Computer;
    orgId: string;
    onClose: () => void;
    onAdded: () => void;
}) {
    const [name, setName] = useState(agent.name);
    /* Unpinned unless somebody picks: the agent's own default is what it
       runs on that computer already, and the first model of its list is
       only the first model of its list. */
    const [settings, setSettings] = useState<AgentSettings>({ model: "", selections: {} });
    const [shared, setShared] = useState(false);
    const [error, setError] = useState("");

    const add = useMutation({
        mutationFn: () => source.addLocalAgent(orgId, agent.id, {
            name: name.trim(),
            model: settings.model,
            selections: agentSettingsChanges({ model: "", selections: {} }, settings).config_selections ?? {},
            shared,
        }),
        onSuccess: () => { onAdded(); onClose(); },
        onError: (problem) => setError(problem instanceof Error ? problem.message : "That could not be added."),
    });

    return (
        <Modal
            title={"Add " + agent.name}
            subtitle={"on " + computer.name}
            onClose={onClose}
            footer={<>
                <button type="button" className="b b--l b--secondary" onClick={onClose}>Cancel</button>
                <button
                    type="button"
                    className="b b--l b--primary"
                    disabled={add.isPending || !name.trim()}
                    onClick={() => { setError(""); add.mutate(); }}
                >
                    {add.isPending ? "Adding…" : "Add"}
                </button>
            </>}
        >
            <div className="kset-form">
                <label className="kfield">
                    <span>Name</span>
                    <input className="kinput" value={name} onChange={(event) => setName(event.target.value)} />
                </label>
                <AgentSettingsFields agent={agent} computer={computer} settings={settings} onChange={setSettings} />
                <label className="kset-check">
                    <input type="checkbox" checked={shared} onChange={(event) => setShared(event.target.checked)} />
                    <span>
                        Let everyone in this organization pick it
                        {/* The one setting here that hands your machine to
                            other people, so it is off until it is read. */}
                        <em>Runs on {computer.name}, signed in as you, with your files in reach.</em>
                    </span>
                </label>
                {error && <Problem>{error}</Problem>}
            </div>
        </Modal>
    );
}

/** What a Test found: the route answered with these models, or it did not. */
type Tested = { ok: true; models: string[] } | { ok: false; message: string };

/** Connecting a provider's key: pick the provider, and the address fills in;
 *  the key, and optionally which models and which of them read images. One
 *  dialog, its two buttons at the foot. */
function AddKey({ orgId, onClose, onAdded }: { orgId: string; onClose: () => void; onAdded: () => void }) {
    const [preset, setPreset] = useState(PRESETS[0]);
    const [name, setName] = useState(PRESETS[0].name);
    const [baseUrl, setBaseUrl] = useState(PRESETS[0].baseUrl);
    const [apiKey, setApiKey] = useState("");
    const [models, setModels] = useState("");
    const [vision, setVision] = useState<string[]>([]);
    const [tested, setTested] = useState<Tested | null>(null);
    const [error, setError] = useState("");
    /* Testing goes through this computer's own model lookup, which only the
       Lemma app has. A browser saves and lets the backend discover. */
    const canTest = useThisMacAvailability() === "shown";
    const presets = canTest ? [...PRESETS, OLLAMA_PRESET] : PRESETS;
    const sentKey = keyToSend(apiKey, baseUrl, LOCAL_SERVER_KEY);

    const pick = (chosen: typeof PRESETS[number]) => {
        setPreset(chosen);
        setName(chosen.name);
        setBaseUrl(chosen.baseUrl);
        setTested(null);
    };

    const typed = modelNames(models);
    const candidates = visionCandidates(typed, tested?.ok ? tested.models : []);

    const test = useMutation({
        mutationFn: async () => readDiscoveredModels(
            await thisMac.discoverModels(discoveryRequest(preset.protocol, baseUrl, apiKey)),
        ),
        onSuccess: (found) => setTested(
            found.length > 0
                ? { ok: true, models: found }
                : { ok: false, message: "The route answered, but listed no models. Name them under Models." },
        ),
        onError: (problem) => setTested({ ok: false, message: testFailureMessage(name.trim() || preset.name, friendlyError(problem)) }),
    });

    const add = useMutation({
        mutationFn: () => source.addProviderKey(orgId, {
            protocol: preset.protocol,
            name: name.trim(),
            baseUrl: baseUrl.trim(),
            apiKey: sentKey,
            models: typed,
            visionModels: chosenVisionModels(preset.protocol, vision, candidates),
        }),
        onSuccess: () => { onAdded(); onClose(); },
        onError: (problem) => setError(problem instanceof Error ? problem.message : "That key could not be saved."),
    });

    const toggleVision = (model: string) =>
        setVision((was) => (was.includes(model) ? was.filter((one) => one !== model) : [...was, model]));

    const address = (
        <div className="kfield">
            <label htmlFor="add-key-address">API address</label>
            <input
                id="add-key-address"
                className="kinput"
                value={baseUrl}
                placeholder="https://…"
                spellCheck={false}
                autoComplete="off"
                {...NOT_A_LOGIN}
                onChange={(event) => { setBaseUrl(event.target.value); setTested(null); }}
            />
            <small>Where {preset.name}&rsquo;s API answers. Filled in for you.</small>
        </div>
    );

    return (
        <Modal
            title="Connect a key"
            subtitle="Billed to you, shared across this organization"
            onClose={onClose}
            footer={<>
                <button type="button" className="b b--l b--secondary" onClick={onClose}>Cancel</button>
                <button
                    type="button"
                    className="b b--l b--primary"
                    disabled={add.isPending || !name.trim() || !sentKey}
                    onClick={() => { setError(""); add.mutate(); }}
                >
                    {add.isPending ? "Saving…" : "Connect"}
                </button>
            </>}
        >
            <div className="kset-form">
                <div className="kset-range" role="group" aria-label="Provider">
                    {presets.map((one) => (
                        <button
                            key={one.id}
                            type="button"
                            className="kchip"
                            aria-pressed={one.id === preset.id}
                            onClick={() => pick(one)}
                        >
                            {one.name}
                        </button>
                    ))}
                </div>
                <label className="kfield">
                    <span>Name</span>
                    <input className="kinput" value={name} onChange={(event) => setName(event.target.value)} />
                </label>
                {!preset.baseUrl && address}
                <div className="kfield">
                    <label htmlFor="add-key-secret">
                        API key{isLocalRoute(baseUrl) && <span className="kfield__opt"> (not needed on this computer)</span>}
                    </label>
                    <SecretInput
                        id="add-key-secret"
                        label="API key"
                        value={apiKey}
                        onChange={(event) => { setApiKey(event.target.value); setTested(null); }}
                    />
                </div>
                <label className="kfield">
                    <span>Models <span className="kfield__opt">(optional)</span></span>
                    <input
                        className="kinput"
                        value={models}
                        placeholder="gpt-5, o3-mini"
                        onChange={(event) => setModels(event.target.value)}
                    />
                    <small>Comma separated. Left empty, the provider&rsquo;s own list is used.</small>
                </label>
                {/* Each provider's address is filled in by picking it, so
                    it is changed only for a proxy or a self-hosted route. */}
                {preset.baseUrl && <MoreSettings key={preset.id}>{address}</MoreSettings>}
                {canTest && (
                    <div className="kset-acts">
                        <button
                            type="button"
                            className="b b--s b--secondary"
                            disabled={test.isPending || !baseUrl.trim()}
                            onClick={() => test.mutate()}
                        >
                            {test.isPending ? "Testing…" : "Test this key"}
                        </button>
                        {tested && (
                            <span role="status" className={"kset-said" + (tested.ok ? "" : " kset-said--bad")}>
                                {tested.ok
                                    ? "Connected. It lists " + tested.models.length + (tested.models.length === 1 ? " model." : " models.")
                                    : tested.message}
                            </span>
                        )}
                    </div>
                )}
                {/* Asked only where the route cannot say: an OpenAI-style model
                    list carries no modalities, and handing an image to a model
                    that cannot read one breaks the conversation. */}
                {asksAboutImages(preset.protocol) && candidates.length > 0 && (
                    <div className="kfield" role="group" aria-label="Reads images">
                        <span>Reads images <span className="kfield__opt">(optional)</span></span>
                        {candidates.map((model) => (
                            <label className="kset-check" key={model}>
                                <input type="checkbox" checked={vision.includes(model)} onChange={() => toggleVision(model)} />
                                <span>{model}</span>
                            </label>
                        ))}
                        <small>Ticked models are handed pictures and PDF pages directly. The rest are never sent one.</small>
                    </div>
                )}
                {error && <Problem>{error}</Problem>}
            </div>
        </Modal>
    );
}

/** Settings → Models: the keys and built-in models first, as rows with their
 *  standing and what can be done to each; then each computer, under its own
 *  subheading, with the coding agents it found. The buttons that act on the
 *  whole list sit above it. */
export function ModelsSection({ orgId }: { orgId: string }) {
    const queryClient = useQueryClient();
    const [showRetired, setShowRetired] = useState(false);
    const [addingKey, setAddingKey] = useState(false);
    /* The list as it stood when a key was just added, kept until the
       refreshed list arrives, so the page can tell whether that key was the
       first thing able to answer. */
    const [addedTo, setAddedTo] = useState<Runtime[] | null>(null);

    const runtimes = useQuery({
        queryKey: ["runtimes", orgId],
        queryFn: () => source.listRuntimes(orgId),
    });
    const chosen = useQuery({
        queryKey: ["organization-default", orgId],
        queryFn: () => source.organizationDefault(orgId),
    });
    const computers = useQuery({
        queryKey: ["computers"],
        queryFn: () => source.listComputers(),
        refetchInterval: (query) => computerPoll(query.state.data),
        refetchOnWindowFocus: true,
    });

    /* Which model servers answer on this computer, to say so beside a saved
       local provider that has stopped. Shares ThisMacModelSuggestions' cache,
       and asks nothing outside the Lemma app on its own machine. */
    const onThisMac = useThisMacAvailability() === "shown";
    const localServers = useQuery({
        queryKey: ["this-mac-model-servers"],
        queryFn: () => detectLocalServers(),
        enabled: onThisMac,
        staleTime: 60_000,
        retry: 0,
    });

    const refresh = () => {
        void queryClient.invalidateQueries({ queryKey: ["runtimes", orgId] });
        void queryClient.invalidateQueries({ queryKey: ["organization-default", orgId] });
        void queryClient.invalidateQueries({ queryKey: ["computers"] });
        void queryClient.invalidateQueries({ queryKey: ["this-mac-model-servers"] });
    };

    const all = useMemo(() => runtimes.data ?? [], [runtimes.data]);
    const machines = useMemo(() => computers.data ?? [], [computers.data]);

    /* Which saved runtime belongs to which live agent, so an agent's row can
       say what it is pickable as instead of appearing twice. */
    const savedByAgent = useMemo(() => {
        const map = new Map<string, Runtime>();
        for (const runtime of all) if (runtime.harnessId) map.set(runtime.harnessId, runtime);
        return map;
    }, [all]);

    const known = useMemo(() => new Set(machines.flatMap((one) => one.agents.map((agent) => agent.id))), [machines]);
    /* What is left after the computers have drawn their own: provider keys,
       and agent runtimes whose machine is not in this list. */
    const loose = all.filter((runtime) => !runtime.harnessId || !known.has(runtime.harnessId));
    const retired = loose.filter((runtime) => runtime.archived).length;
    const rows = loose.filter((runtime) => showRetired || !runtime.archived);

    /* Asked only once the chosen default is known: "nobody has chosen" read
       off a pending query would offer to replace a choice already made. */
    const offer = addedTo && chosen.isSuccess && !runtimes.isFetching
        ? firstProviderOffer(addedTo, all, chosen.data)
        : null;
    /* Anything else done to the list afterwards answers the offer too:
       without this, retiring the default later would bring it back. */
    const changed = () => { setAddedTo(null); refresh(); };
    const added = () => { setAddedTo(all); refresh(); };
    const useOffer = useMutation({
        mutationFn: (runtime: Runtime) => source.setOrganizationDefault(orgId, { runtimeId: runtime.id, model: "" }),
        onSuccess: changed,
    });

    const available = all.filter((runtime) => !runtime.archived && !runtime.trouble).length;
    const troubled = all.filter((runtime) => !runtime.archived && runtime.trouble).length;
    const reading = runtimes.isPending || computers.isPending;

    /* Inside the desktop app, the computer this app runs on leads the list with
       its own live status, and is not drawn a second time below. */
    const desktop = useIsDesktop();
    const noun = useThisComputer();
    const thisHostId = useThisHostId();
    const mine = machines.find((computer) => computer.id === thisHostId) ?? null;
    const others = machines.filter((computer) => computer !== mine);

    /* One computer's agents as rows, drawn the same way wherever the computer
       is. Only this one can be told to look again, and only it is somewhere
       the reader can type a command right now, so only it says so. */
    const agentsOf = (computer: Computer) => {
        const here = computer === mine ? noun : null;
        if (stillLooking(computer)) {
            return (
                <li className="krow">
                    <span className="krow__v"><span><LoadingIndicator label="Finding coding agents" /></span></span>
                </li>
            );
        }
        if (computer.agents.length === 0) {
            return (
                <li className="krow">
                    <span className="krow__v kset-wrap">
                        <span>
                            {!computer.online
                                ? "Nothing published. It reports what it finds when it is next awake."
                                : here
                                    ? <>No coding agents found. Install Claude Code, Codex, Cursor or OpenCode on {here}, then check again.</>
                                    : "No coding agents found. Install Claude Code, Codex, Cursor or OpenCode on that computer and it shows up here."}
                        </span>
                    </span>
                    <span className="krow__end">{here && computer.online && <CheckAgainButton />}</span>
                </li>
            );
        }
        return (
            <>
                {computer.agents.map((agent) => (
                    <AgentRow
                        key={agent.id}
                        agent={agent}
                        computer={computer}
                        here={here}
                        saved={savedByAgent.get(agent.id) ?? null}
                        orgId={orgId}
                        onChanged={refresh}
                    />
                ))}
                {here && computer.online && computer.agents.some((agent) => !agent.ready) && (
                    <li className="krow">
                        <span className="krow__v kset-wrap"><span>Signed in or updated one of these? Look again now instead of waiting.</span></span>
                        <span className="krow__end"><CheckAgainButton /></span>
                    </li>
                )}
            </>
        );
    };

    const fetching = runtimes.isFetching || computers.isFetching;

    return (
        <div className="kset-stack">
            {/* Outside the success gate: a listing that failed is exactly
                when "Connect a key" and "Refresh" are needed. */}
            <div className="kset-acts">
                <button type="button" className="b b--s b--secondary" onClick={() => setAddingKey(true)}>
                    <PlusIcon /> Connect a key
                </button>
                <button type="button" className="b b--s b--quiet" onClick={refresh} disabled={fetching}>
                    <RefreshIcon className={fetching ? "spin" : undefined} /> Refresh
                </button>
                {/* Offered only when there is something behind it. A
                    permanent toggle is an invitation to look at nothing. */}
                {retired > 0 && (
                    <button type="button" className="b b--s b--quiet" onClick={() => setShowRetired((was) => !was)}>
                        {showRetired ? "Hide retired" : "Show retired (" + retired + ")"}
                    </button>
                )}
                {/* The count, which the heading cannot know because it does
                    not do the reading. */}
                {runtimes.isSuccess && (
                    <span className="kset-note">
                        {(available || "Nothing") + " to pick from" + (troubled ? " · " + troubled + " needing attention" : "")}
                    </span>
                )}
            </div>

            {reading && <p className="kset-note" role="status">Reading the models…</p>}
            {runtimes.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    {saidAbout(runtimes.error, "Couldn’t load models.")}
                    <button type="button" className="b b--s b--secondary" onClick={refresh} disabled={runtimes.isFetching}>Try again</button>
                </p>
            )}

            {runtimes.isSuccess && (
                <>
                    {available === 0 && (
                        /* The one state where this page is the blocker: every
                           Lemma in the organization is waiting on it. */
                        <div className="kneed" role="status">
                            <div className="krow">
                                <span className="krow__lead">
                                    <span className="kmark" aria-hidden="true"><KeyIcon /></span>
                                    <span className="krow__v kset-wrap">
                                        <b>Add a model to get answers</b>
                                        <span>
                                            Until one is added here, every message comes back unanswered.
                                            Connect a provider&rsquo;s API key, or run a model yourself with{" "}
                                            <a href="https://ollama.com/download" target="_blank" rel="noreferrer">Ollama</a> or{" "}
                                            <a href="https://lmstudio.ai" target="_blank" rel="noreferrer">LM Studio</a>
                                            {onThisMac ? " — once it is running on this computer it shows up below." : " on the computer Lemma runs on."}
                                        </span>
                                    </span>
                                </span>
                                <span className="krow__end">
                                    {onThisMac && (
                                        <button type="button" className="b b--s b--secondary" onClick={refresh} disabled={localServers.isFetching}>
                                            <RefreshIcon className={localServers.isFetching ? "spin" : undefined} /> Check again
                                        </button>
                                    )}
                                </span>
                            </div>
                        </div>
                    )}

                    {offer && (
                        /* The first key is what makes this organization able
                           to answer at all; saying so once, right after it
                           lands, beats leaving every Lemma on a guess. */
                        <div className="kneed" role="status">
                            <div className="krow">
                                <span className="krow__lead">
                                    <span className="kmark" aria-hidden="true"><KeyIcon /></span>
                                    <span className="krow__v kset-wrap">
                                        <b>Use {offer.name} as the default?</b>
                                        <span>Anything that doesn&rsquo;t name a model will run on it. You can change this on any key.</span>
                                        {useOffer.isError && (
                                            <span className="kset-said--bad" role="alert">
                                                {saidAbout(useOffer.error, "That could not be made the default.")}
                                            </span>
                                        )}
                                    </span>
                                </span>
                                <span className="krow__end">
                                    <button type="button" className="b b--s b--secondary" disabled={useOffer.isPending} onClick={() => useOffer.mutate(offer)}>
                                        {useOffer.isPending ? "Saving…" : "Use it"}
                                    </button>
                                    <button type="button" className="b b--s b--quiet" onClick={() => setAddedTo(null)}>Not now</button>
                                </span>
                            </div>
                        </div>
                    )}

                    {rows.length > 0 && (
                        <ul className="klist">
                            {rows.map((runtime) => (
                                <RuntimeRow
                                    key={runtime.id}
                                    runtime={runtime}
                                    orgId={orgId}
                                    onChanged={changed}
                                    isDefault={chosen.data?.runtimeId === runtime.id}
                                    answering={onThisMac && localServers.isSuccess ? localRouteAnswering(runtime.baseUrl, localServers.data) : null}
                                />
                            ))}
                        </ul>
                    )}

                    {/* On a local install, the model servers already running
                        on this computer and the provider it was set up with,
                        each one click from being picked here. Draws nothing
                        anywhere else. */}
                    <ThisMacModelSuggestions orgId={orgId} runtimes={all} onAdded={added} />

                    {desktop && (
                        <ThisComputerCard release={mine?.release}>
                            {mine && agentsOf(mine)}
                        </ThisComputerCard>
                    )}

                    {others.map((computer) => (
                        <section className="kset-stack" key={computer.id} aria-label={computer.name}>
                            <p className="ksub">
                                {computer.name}
                                <span>
                                    {[
                                        computer.release ? "Lemma app " + computer.release : null,
                                        !computer.online && computer.lastSeen
                                            ? "last seen " + new Date(computer.lastSeen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                                            : null,
                                    ].filter(Boolean).join(" · ")}
                                </span>
                                <span className={statusClass(computer.online ? "ok" : "muted")}>{computer.status}</span>
                            </p>
                            <ul className="klist">{agentsOf(computer)}</ul>
                        </section>
                    ))}

                    {computers.isSuccess && machines.length === 0 && !desktop && (
                        /* A browser has no computer to offer: Agent Host ships
                           inside the desktop app and is supervised by it, so
                           this is a handoff rather than a wizard. */
                        <>
                            <p className="ksub">On your computers <span>Coding agents that run where they’re signed in</span></p>
                            <ul className="klist">
                                <li className="krow">
                                    <span className="krow__lead">
                                        <span className="kmark" aria-hidden="true"><TerminalIcon /></span>
                                        <span className="krow__v kset-wrap">
                                            <b>Run on Claude Code or Codex</b>
                                            <span>
                                                They already live on your machine. Install the Lemma app there and sign in —
                                                it connects itself, and the agents it finds appear here.
                                            </span>
                                        </span>
                                    </span>
                                    <span className="krow__end">
                                        {downloadUrl() && (
                                            <a className="b b--s b--secondary" href={downloadUrl()} target="_blank" rel="noreferrer">
                                                <DownloadIcon /> Get the app
                                            </a>
                                        )}
                                    </span>
                                </li>
                            </ul>
                        </>
                    )}

                    {computers.isError && (
                        <p className="kset-said kset-said--bad" role="alert">
                            Couldn’t load your computers.
                            <button type="button" className="b b--s b--secondary" onClick={() => void computers.refetch()}>Try again</button>
                        </p>
                    )}

                    {troubled > 0 && (
                        <p className="kset-note">
                            Anything pinned to something unavailable stops answering
                            until that computer is back or you point it somewhere else.
                        </p>
                    )}
                </>
            )}

            {addingKey && (
                <AddKey
                    orgId={orgId}
                    onClose={() => setAddingKey(false)}
                    onAdded={added}
                />
            )}
        </div>
    );
}
