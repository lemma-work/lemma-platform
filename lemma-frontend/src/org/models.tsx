import { LoadingIndicator } from "@/ui/loading";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    source,
    agentLogo,
    agentSettingsChanges,
    stillLooking,
    type AgentSettings,
    type Computer,
    type LocalAgent,
    type Runtime,
} from "@/data";
import { downloadUrl } from "@/session/client";
import { Modal } from "@/shell/modal";
import { useIsDesktop } from "@/desktop/bridge";
import { ThisComputerCard, useThisHostId } from "@/desktop/this-computer-card";
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
}: {
    mark: React.ReactNode;
    name: string;
    detail?: string;
    tag?: string;
    note?: string;
    state: string;
    tone: "ok" | "warn" | "muted";
    action?: React.ReactNode;
    quiet?: boolean;
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
            </span>
            {action}
            <span className={"mrow__state mrow__state--" + tone}>
                <i aria-hidden="true" />
                {state}
            </span>
        </li>
    );
}

function modelCount(count: number): string {
    return count ? count + (count === 1 ? " model" : " models") : "";
}

/** A saved runtime with no live agent behind it: a provider key, or a coding
 *  agent whose computer is not in this list. */
function RuntimeRow({ runtime, orgId, onChanged }: { runtime: Runtime; orgId: string; onChanged: () => void }) {
    const [confirming, setConfirming] = useState(false);
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
            tag={runtime.scope === "personal" ? "yours" : undefined}
            state={runtime.archived ? "Retired" : runtime.trouble || "Available"}
            tone={runtime.archived ? "muted" : runtime.trouble ? "warn" : "ok"}
            quiet={runtime.archived}
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
                    <button className="linkish mrow__quiet" onClick={() => setConfirming(true)}>Retire</button>
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
    saved,
    orgId,
    onChanged,
}: {
    agent: LocalAgent;
    computer: Computer;
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
                note={computer.online && !agent.ready ? agent.fix : undefined}
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

function AddKey({ orgId, onClose, onAdded }: { orgId: string; onClose: () => void; onAdded: () => void }) {
    const [preset, setPreset] = useState(PRESETS[0]);
    const [name, setName] = useState(PRESETS[0].name);
    const [baseUrl, setBaseUrl] = useState(PRESETS[0].baseUrl);
    const [apiKey, setApiKey] = useState("");
    const [models, setModels] = useState("");
    const [error, setError] = useState("");

    const pick = (chosen: typeof PRESETS[number]) => {
        setPreset(chosen);
        setName(chosen.name);
        setBaseUrl(chosen.baseUrl);
    };

    const add = useMutation({
        mutationFn: () => source.addProviderKey(orgId, {
            protocol: preset.protocol,
            name: name.trim(),
            baseUrl: baseUrl.trim(),
            apiKey: apiKey.trim(),
            models: models.split(",").map((one) => one.trim()).filter(Boolean),
        }),
        onSuccess: () => { onAdded(); onClose(); },
        onError: (problem) => setError(problem instanceof Error ? problem.message : "That key could not be saved."),
    });

    return (
        <Modal title="Connect a key" subtitle="Billed to you, shared with every teammate here" narrow onClose={onClose}>
            <div className="presets" role="group" aria-label="Provider">
                {PRESETS.map((one) => (
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
                <label htmlFor="key-url">Route</label>
                <input id="key-url" value={baseUrl} placeholder="https://…" onChange={(event) => setBaseUrl(event.target.value)} />
            </div>
            <div className="field">
                <label htmlFor="key-secret">API key</label>
                <input id="key-secret" type="password" value={apiKey} autoComplete="off" onChange={(event) => setApiKey(event.target.value)} />
            </div>
            <div className="field">
                <label htmlFor="key-models">Models <em>optional</em></label>
                <input
                    id="key-models"
                    value={models}
                    placeholder="gpt-5, o3-mini"
                    onChange={(event) => setModels(event.target.value)}
                />
                <span>Comma separated. Left empty, the route&rsquo;s own list is used.</span>
            </div>
            {error && <p className="reachrow__error">{error}</p>}
            <div className="modal__acts">
                <button className="linkish" onClick={onClose}>Cancel</button>
                <button
                    className="btn btn--primary"
                    disabled={add.isPending || !name.trim() || !apiKey.trim()}
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

    const runtimes = useQuery({
        queryKey: ["runtimes", orgId],
        queryFn: () => source.listRuntimes(orgId),
    });
    const computers = useQuery({
        queryKey: ["computers"],
        queryFn: () => source.listComputers(),
        refetchInterval: (query) => computerPoll(query.state.data),
        refetchOnWindowFocus: true,
    });

    const refresh = () => {
        void queryClient.invalidateQueries({ queryKey: ["runtimes", orgId] });
        void queryClient.invalidateQueries({ queryKey: ["computers"] });
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

    const available = all.filter((runtime) => !runtime.archived && !runtime.trouble).length;
    const troubled = all.filter((runtime) => !runtime.archived && runtime.trouble).length;
    const reading = runtimes.isPending || computers.isPending;

    /* Inside the desktop app, the computer this app runs on leads the list with
       its own live status, and is not drawn a second time below. */
    const desktop = useIsDesktop();
    const thisHostId = useThisHostId();
    const mine = machines.find((computer) => computer.id === thisHostId) ?? null;
    const others = machines.filter((computer) => computer !== mine);

    /* One computer's agents, drawn the same way wherever the computer is. */
    const agentsOf = (computer: Computer) => (
        stillLooking(computer) ? (
            <p className="mgroup__empty">
                <LoadingIndicator label="Finding coding agents" />
            </p>
        ) : computer.agents.length === 0 ? (
            <p className="mgroup__empty">
                {computer.online
                    ? "No coding agents found. Install Claude Code, Codex, Cursor or OpenCode there and it shows up here."
                    : "Nothing published. It reports what it finds when it is next awake."}
            </p>
        ) : (
            <ul className="mlist">
                {computer.agents.map((agent) => (
                    <AgentRow
                        key={agent.id}
                        agent={agent}
                        computer={computer}
                        saved={savedByAgent.get(agent.id) ?? null}
                        orgId={orgId}
                        onChanged={refresh}
                    />
                ))}
            </ul>
        )
    );

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
            {runtimes.isError && <p className="empty-row">Couldn’t load models.</p>}

            {runtimes.isSuccess && (
                <>
                    {rows.length > 0 && (
                        <ul className="mlist">
                            {rows.map((runtime) => (
                                <RuntimeRow key={runtime.id} runtime={runtime} orgId={orgId} onChanged={refresh} />
                            ))}
                        </ul>
                    )}

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

                    {troubled > 0 && (
                        <p className="connectors__note">
                            <WarningIcon size={13} /> A teammate pinned to something unavailable stops answering
                            until that computer is back or you point it somewhere else.
                        </p>
                    )}
                </>
            )}

            {addingKey && <AddKey orgId={orgId} onClose={() => setAddingKey(false)} onAdded={refresh} />}
        </div>
    );
}
