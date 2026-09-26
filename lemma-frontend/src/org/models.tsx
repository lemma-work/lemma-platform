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
import { CheckAgainButton, ThisComputerCard, useThisHostId } from "@/desktop/this-computer-card";
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
    ComputerIcon,
    DownloadIcon,
    KeyIcon,
    PlusIcon,
    RefreshIcon,
    LemmaMark,
    TerminalIcon,
    WarningIcon,
} from "@/ui/icons";

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
 *  while anything is unsettled, slowly once everything is online — without
 *  this a machine sits at "Offline" until the window is refocused. */
function computerPoll(computers: Computer[] | undefined): number {
    if (!computers) return 4_000;
    const unsettled = computers.length === 0 || computers.some((one) => !one.online || stillLooking(one));
    return unsettled ? 4_000 : 20_000;
}

function Mark({ runtime }: { runtime: { harness: string; kind: "key" | "agent"; scope?: string } }) {
    const logo = agentLogo(runtime.harness);
    if (logo) return <img className="mrow__logo" src={logo} alt="" aria-hidden="true" />;
    if (runtime.scope === "system") return <span className="mrow__mark"><LemmaMark size={14} /></span>;
    if (runtime.kind === "agent") return <span className="mrow__mark"><TerminalIcon size={15} /></span>;
    return <span className="mrow__mark"><KeyIcon size={15} /></span>;
}

/** One row, whatever fills it.
 *
 *  A bought key, a coding agent on a laptop and an agent nobody has added yet
 *  are one row on purpose: the question the reader is asking is the same for
 *  all three — can a conversation pick this now — so they answer it in the
 *  same place, in the same words. */
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
        <li className={"mrow" + (quiet ? " mrow--quiet" : "")}>
            {mark}
            <span className="mrow__body">
                <span className="mrow__line">
                    <span className="mrow__name">{name}</span>
                    {detail && <span className="mrow__detail">{detail}</span>}
                    {tag && <span className="pill">{tag}</span>}
                </span>
                {note && <span className="mrow__note">{note}</span>}
                {result && (
                    <span role="status" className={"mrow__note" + (result.ok ? "" : " reachrow__error")}>
                        {result.message}
                    </span>
                )}
            </span>
            {action}
            <span className={"mrow__state mrow__state--" + tone}>
                <i aria-hidden="true" />
                {state}
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
            tag={isDefault ? "Default" : runtime.scope === "personal" ? "yours" : undefined}
            note={!runtime.archived && answering === false
                ? "Nothing is answering at this address. Start the model server on this computer, then check again."
                : undefined}
            state={runtime.archived ? "Retired" : runtime.trouble || (answering === false ? "Not answering" : "Available")}
            tone={runtime.archived ? "muted" : runtime.trouble || answering === false ? "warn" : "ok"}
            quiet={runtime.archived}
            result={result}
            action={
                runtime.scope === "system" ? undefined : runtime.archived ? (
                    <button className="linkish" disabled={restore.isPending} onClick={() => restore.mutate()}>
                        {restore.isPending ? "Bringing back…" : "Bring back"}
                    </button>
                ) : confirming ? (
                    <span className="mrow__confirm">
                        <button className="btn" disabled={archive.isPending} onClick={() => archive.mutate()}>
                            {archive.isPending ? "Retiring…" : "Retire"}
                        </button>
                        <button className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                    </span>
                ) : (
                    <span className="mrow__confirm">
                        {testable && (
                            <button className="linkish" disabled={check.isPending} onClick={() => check.mutate()}>
                                {check.isPending ? "Testing…" : "Test"}
                            </button>
                        )}
                        {canBeDefault && (
                            <button className="linkish" disabled={makeDefault.isPending} onClick={() => makeDefault.mutate()}>
                                {makeDefault.isPending ? "Saving…" : "Make default"}
                            </button>
                        )}
                        <button className="linkish mrow__quiet" onClick={() => setConfirming(true)}>Retire</button>
                    </span>
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
                tag={saved?.scope === "personal" ? "yours" : saved?.scope === "org" ? "shared" : undefined}
                /* Said only when the computer itself is reachable. When it is
                   not, its own heading already said so, and repeating it under
                   every agent is the same sentence three times. */
                note={computer.online && !agent.ready ? withCode(agentFix(agent, here)) : undefined}
                state={state}
                tone={tone}
                quiet={!computer.online}
                action={
                    saved?.archived ? (
                        <button className="linkish" disabled={restore.isPending} onClick={() => restore.mutate()}>
                            {restore.isPending ? "Bringing back…" : "Bring back"}
                        </button>
                    ) : added && usable ? (
                        <button className="linkish" onClick={() => setEditing(true)}>Settings</button>
                    ) : added || !usable ? undefined : (
                        /* Offered only while that computer can actually take
                           it. Adding binds the runtime to the live agent — the
                           backend asks the machine what it offers — so a
                           sleeping laptop would mean filling in a dialog and
                           then failing on save. */
                        <button className="btn mrow__add" onClick={() => setAdding(true)}>
                            <PlusIcon size={13} /> Add
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
        <Modal title={"Add " + agent.name} subtitle={"on " + computer.name} narrow onClose={onClose}>
            <div className="field">
                <label htmlFor="agent-name">Name</label>
                <input id="agent-name" value={name} onChange={(event) => setName(event.target.value)} />
            </div>
            <AgentSettingsFields agent={agent} computer={computer} settings={settings} onChange={setSettings} />
            <label className="check">
                <input type="checkbox" checked={shared} onChange={(event) => setShared(event.target.checked)} />
                <span>
                    Let everyone in this organization pick it
                    {/* The one setting here that hands your machine to other
                        people, so it is off until it is read. */}
                    <em>Runs on {computer.name}, signed in as you, with your files in reach.</em>
                </span>
            </label>
            {error && <p className="reachrow__error">{error}</p>}
            <div className="modal__acts">
                <button className="linkish" onClick={onClose}>Cancel</button>
                <button
                    className="btn btn--primary"
                    disabled={add.isPending || !name.trim()}
                    onClick={() => { setError(""); add.mutate(); }}
                >
                    {add.isPending ? "Adding…" : "Add"}
                </button>
            </div>
        </Modal>
    );
}

/** What a Test found: the route answered with these models, or it did not. */
type Tested = { ok: true; models: string[] } | { ok: false; message: string };

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

    return (
        <Modal title="Connect a key" subtitle="Billed to you, shared with every teammate here" narrow onClose={onClose}>
            <div className="presets" role="group" aria-label="Provider">
                {presets.map((one) => (
                    <button
                        key={one.id}
                        className={"preset" + (one.id === preset.id ? " preset--on" : "")}
                        aria-pressed={one.id === preset.id}
                        onClick={() => pick(one)}
                    >
                        {one.name}
                    </button>
                ))}
            </div>
            <div className="field">
                <label htmlFor="key-name">Name</label>
                <input id="key-name" value={name} onChange={(event) => setName(event.target.value)} />
            </div>
            <div className="field">
                <label htmlFor="key-url">API base URL</label>
                <input
                    id="key-url"
                    value={baseUrl}
                    placeholder="https://…"
                    onChange={(event) => { setBaseUrl(event.target.value); setTested(null); }}
                />
            </div>
            <div className="field">
                <label htmlFor="key-secret">API key{isLocalRoute(baseUrl) && <em> not needed on this computer</em>}</label>
                <input
                    id="key-secret"
                    type="password"
                    value={apiKey}
                    autoComplete="off"
                    onChange={(event) => { setApiKey(event.target.value); setTested(null); }}
                />
            </div>
            <div className="field">
                <label htmlFor="key-models">Models <em>optional</em></label>
                <input
                    id="key-models"
                    value={models}
                    placeholder="gpt-5, o3-mini"
                    onChange={(event) => setModels(event.target.value)}
                />
                <span>Comma separated. Left empty, the provider&rsquo;s own list is used.</span>
            </div>
            {canTest && (
                <div className="field">
                    <button
                        className="linkish"
                        disabled={test.isPending || !baseUrl.trim()}
                        onClick={() => test.mutate()}
                    >
                        {test.isPending ? "Testing…" : "Test this key"}
                    </button>
                    {tested && (
                        <span role="status" className={tested.ok ? undefined : "reachrow__error"}>
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
                <div className="field" role="group" aria-labelledby="key-vision">
                    <label id="key-vision">Reads images <em>optional</em></label>
                    {candidates.map((model) => (
                        <label className="check" key={model}>
                            <input type="checkbox" checked={vision.includes(model)} onChange={() => toggleVision(model)} />
                            <span>{model}</span>
                        </label>
                    ))}
                    <span>Ticked models are handed pictures and PDF pages directly. The rest are never sent one.</span>
                </div>
            )}
            {error && <p className="reachrow__error">{error}</p>}
            <div className="modal__acts">
                <button className="linkish" onClick={onClose}>Cancel</button>
                <button
                    className="btn btn--primary"
                    disabled={add.isPending || !name.trim() || !sentKey}
                    onClick={() => { setError(""); add.mutate(); }}
                >
                    {add.isPending ? "Saving…" : "Connect"}
                </button>
            </div>
        </Modal>
    );
}

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

    /* One computer's agents, drawn the same way wherever the computer is.
       Only this one can be told to look again, and only it is somewhere the
       reader can type a command right now, so only it says so. */
    const agentsOf = (computer: Computer) => {
        const here = computer === mine ? noun : null;
        if (stillLooking(computer)) {
            return (
                <p className="mgroup__empty">
                    <LoadingIndicator label="Finding coding agents" />
                </p>
            );
        }
        if (computer.agents.length === 0) {
            return (
                <div className="mgroup__empty">
                    {!computer.online
                        ? "Nothing published. It reports what it finds when it is next awake."
                        : here
                            ? <>No coding agents found. Install Claude Code, Codex, Cursor or OpenCode on {here}, then press Check again. <CheckAgainButton /></>
                            : "No coding agents found. Install Claude Code, Codex, Cursor or OpenCode on that computer and it shows up here."}
                </div>
            );
        }
        return (
            <>
                <ul className="mlist">
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
                </ul>
                {here && computer.online && computer.agents.some((agent) => !agent.ready) && (
                    <div className="mgroup__empty"><CheckAgainButton /></div>
                )}
            </>
        );
    };

    return (
        <div className="section">
            {/* No heading here: the settings pane names this section and
                carries the lead as its subtitle. What is left is the count,
                which the pane cannot know because it does not do the
                reading. */}
            <p className="section__meta">
                {runtimes.isSuccess
                    ? (available || "Nothing") + " to pick from" + (troubled ? " · " + troubled + " needing attention" : "")
                    : ""}
            </p>

            {reading && <p className="empty-row">Reading…</p>}
            {runtimes.isError && (
                <p className="empty-row" role="alert">
                    {saidAbout(runtimes.error, "Couldn’t load models.")}{" "}
                    <button className="linkish" onClick={refresh} disabled={runtimes.isFetching}>Retry</button>
                </p>
            )}

            {runtimes.isSuccess && (
                <>
                    {available === 0 && (
                        /* The one state where this page is the blocker: every
                           teammate in the organization is waiting on it. */
                        <div className="getapp" role="status">
                            <span className="getapp__mark"><KeyIcon size={18} /></span>
                            <span className="getapp__body">
                                <b>Teammates need a model to think with</b>
                                <span>
                                    Until one is added here, every message to a teammate comes back unanswered.
                                    Connect a provider&rsquo;s API key, or run a model yourself with{" "}
                                    <a href="https://ollama.com/download" target="_blank" rel="noreferrer">Ollama</a> or{" "}
                                    <a href="https://lmstudio.ai" target="_blank" rel="noreferrer">LM Studio</a>
                                    {onThisMac ? " — once it is running on this computer it shows up below." : " on the computer Lemma runs on."}
                                </span>
                            </span>
                            {onThisMac && (
                                <button className="btn" onClick={refresh} disabled={localServers.isFetching}>
                                    <RefreshIcon size={13} className={localServers.isFetching ? "spin" : undefined} /> Check again
                                </button>
                            )}
                        </div>
                    )}

                    {offer && (
                        /* The first key is what makes this organization able
                           to answer at all; saying so once, right after it
                           lands, beats leaving every teammate on a guess. */
                        <div className="getapp" role="status">
                            <span className="getapp__mark"><KeyIcon size={18} /></span>
                            <span className="getapp__body">
                                <b>Use {offer.name} for all teammates?</b>
                                <span>
                                    Teammates that don&rsquo;t name a model will run on it. You can change this on any key.
                                </span>
                                {useOffer.isError && (
                                    <span className="reachrow__error">
                                        {saidAbout(useOffer.error, "That could not be made the default.")}
                                    </span>
                                )}
                            </span>
                            <span className="mrow__confirm">
                                <button className="btn" disabled={useOffer.isPending} onClick={() => useOffer.mutate(offer)}>
                                    {useOffer.isPending ? "Saving…" : "Use it"}
                                </button>
                                <button className="linkish" onClick={() => setAddedTo(null)}>Not now</button>
                            </span>
                        </div>
                    )}

                    {rows.length > 0 && (
                        <ul className="mlist">
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
                        <section className="mgroup" key={computer.id}>
                            <div className="mgroup__head">
                                <ComputerIcon size={14} />
                                <span className="mgroup__name">{computer.name}</span>
                                <span className="mgroup__meta">
                                    {computer.release && "Lemma app " + computer.release}
                                    {computer.online
                                        ? ""
                                        : computer.lastSeen
                                            ? " · last seen " + new Date(computer.lastSeen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                                            : ""}
                                </span>
                                <span className={"mrow__state mrow__state--" + (computer.online ? "ok" : "muted")}>
                                    <i aria-hidden="true" />
                                    {computer.status}
                                </span>
                            </div>
                            {agentsOf(computer)}
                        </section>
                    ))}

                    {computers.isSuccess && machines.length === 0 && !desktop && (
                        /* A browser has no computer to offer: Agent Host ships
                           inside the desktop app and is supervised by it, so
                           this is a handoff rather than a wizard. */
                        <div className="getapp">
                            <span className="getapp__mark"><TerminalIcon size={18} /></span>
                            <span className="getapp__body">
                                <b>Run Claude Code or Codex as this teammate</b>
                                <span>
                                    They already live on your machine. Install the Lemma app there and sign in —
                                    it connects itself, and the agents it finds appear here.
                                </span>
                            </span>
                            {downloadUrl() && (
                                <a className="btn" href={downloadUrl()} target="_blank" rel="noreferrer">
                                    <DownloadIcon size={13} /> Get the app
                                </a>
                            )}
                        </div>
                    )}

                    {computers.isError && (
                        <p className="empty-row">Couldn’t load your computers.</p>
                    )}

                    {troubled > 0 && (
                        <p className="connectors__note">
                            <WarningIcon size={13} /> A teammate pinned to something unavailable stops answering
                            until that computer is back or you point it somewhere else.
                        </p>
                    )}
                </>
            )}

            {/* Outside the success gate: a listing that failed is exactly
                when "Connect a key" and "Refresh" are needed. */}
            <div className="models__acts">
                <button className="btn" onClick={() => setAddingKey(true)}>
                    <PlusIcon size={13} /> Connect a key
                </button>
                <button className="linkish" onClick={refresh} disabled={runtimes.isFetching || computers.isFetching}>
                    <RefreshIcon size={13} className={runtimes.isFetching || computers.isFetching ? "spin" : undefined} /> Refresh
                </button>
                {/* Offered only when there is something behind it. A
                    permanent toggle is an invitation to look at
                    nothing. */}
                {retired > 0 && (
                    <button className="linkish" onClick={() => setShowRetired((was) => !was)}>
                        {showRetired ? "Hide retired" : "Show retired (" + retired + ")"}
                    </button>
                )}
            </div>

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
