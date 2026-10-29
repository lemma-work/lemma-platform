"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Modal } from "@/shell/modal";
import { copyText } from "@/desktop/clipboard";
import {
    CheckIcon, ChevronRightIcon, ComputerIcon, CopyIcon, ExternalIcon, FileIcon, FolderIcon, GlobeIcon, LockIcon,
    MoreIcon, PlusIcon, RefreshIcon, TerminalIcon,
} from "@/ui/icons";
import { readableSize } from "./machine";
import { groupSites, loginNote, saidSoFor } from "./logins";
import type { WebLogin } from "./queries";
import {
    cleanupScopes, computeLabel, groupMachines, isFinished, LINK_TTLS, previewLive, resetLines, SCOPE_WORDS,
    sizeLabel, STATUS_WORD, totalFreed, usageSummary, validMachineName,
    type Machine, type MachinePolicy, type MachineTemplate, type ResetResult, type ResetScope,
} from "./environments";
import {
    useCreatePodMachine, useCreatePreview, useDeletePodMachine, useForgetMachineLogin, useKillProcess,
    useMachineFiles, useMachineLogins, useMachinePolicy, usePreviews, useProcesses, useResetMachine,
    useRevokePreview,
} from "./environments-queries";
import { Terminal } from "./terminal";

function problemText(problem: unknown, fallback: string): string {
    return problem instanceof Error && problem.message ? problem.message : fallback;
}

/* ── the rail ─────────────────────────────────────────────────────── */

/** Every machine this teammate's space can reach, in three groups: yours,
 *  the space's own, and the ones somebody connected. */
export function EnvironmentsRail({ podId, machines, loading, failed, onRetry, selected, onSelect, visible }: {
    podId: string;
    machines: Machine[];
    loading: boolean;
    failed: boolean;
    onRetry: () => void;
    selected: string;
    onSelect: (ref: string) => void;
    visible: boolean;
}) {
    const rail = groupMachines(machines);
    const policy = useMachinePolicy(podId, visible);
    const [creating, setCreating] = useState(false);
    const [deleting, setDeleting] = useState<Machine | null>(null);
    /* Whether this person may add one. The list says per machine what the
       caller may do; with none yet, the offer is made and the server is the
       one that refuses a viewer. */
    const canCreate = rail.pod.length === 0
        ? policy.data !== undefined && policy.data.max_pod_machines !== 0
        : rail.pod.some((machine) => machine.access === "manage");
    const full = typeof policy.data?.max_pod_machines === "number" && rail.pod.length >= policy.data.max_pod_machines;

    return (
        <nav className="envrail" aria-label="Machines">
            {loading && <p className="computer-note" role="status">Finding machines…</p>}
            {failed && (
                <p className="computer-note" role="alert">
                    The machines could not be listed.{" "}
                    <button className="computer-inline" onClick={onRetry}>Try again</button>
                </p>
            )}

            <RailGroup title="My machine">
                {rail.mine.map((machine) => (
                    <RailRow key={machine.ref} machine={machine} selected={selected === machine.ref} onSelect={onSelect} />
                ))}
                {!loading && !failed && rail.mine.length === 0 && <p className="envrail__empty">Not set up yet.</p>}
            </RailGroup>

            <RailGroup title="Pod machines" action={canCreate && !full ? (
                <button className="envrail__add" onClick={() => setCreating(true)} aria-label="Create pod machine" title="Create pod machine">
                    <PlusIcon size={14} />
                </button>
            ) : null}>
                {rail.pod.map((machine) => (
                    <RailRow
                        key={machine.ref}
                        machine={machine}
                        selected={selected === machine.ref}
                        onSelect={onSelect}
                        onDelete={machine.access === "manage" ? () => setDeleting(machine) : undefined}
                    />
                ))}
                {!loading && !failed && rail.pod.length === 0 && (
                    <p className="envrail__empty">
                        A machine of the space&apos;s own, shared by its people and agents.
                        {canCreate && <> <button className="computer-inline" onClick={() => setCreating(true)}>Create one</button></>}
                    </p>
                )}
            </RailGroup>

            <RailGroup title="Connected">
                {rail.connected.map((machine) => (
                    <RailRow key={machine.ref} machine={machine} selected={selected === machine.ref} onSelect={onSelect} />
                ))}
                <ConnectPanel />
            </RailGroup>

            {machines.length > 0 && <p className="envrail__usage">{usageSummary(machines)}</p>}

            {creating && policy.data && (
                <CreateMachine podId={podId} policy={policy.data} onClose={() => setCreating(false)} onCreated={(ref) => { setCreating(false); onSelect(ref); }} />
            )}
            {creating && !policy.data && (
                <Modal title="Create pod machine" narrow onClose={() => setCreating(false)}>
                    <p className="computer-note" role={policy.isError ? "alert" : "status"}>
                        {policy.isError ? "What this space may create could not be read." : "Reading what this space may create…"}
                    </p>
                </Modal>
            )}
            {deleting && (
                <DeleteMachine
                    podId={podId}
                    machine={deleting}
                    onClose={() => setDeleting(null)}
                    onDeleted={() => { if (selected === deleting.ref) onSelect("me"); setDeleting(null); }}
                />
            )}
        </nav>
    );
}

function RailGroup({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
    return (
        <section className="envrail__group">
            <header className="envrail__head">
                <h2>{title}</h2>
                {action}
            </header>
            {children}
        </section>
    );
}

function RailRow({ machine, selected, onSelect, onDelete }: {
    machine: Machine;
    selected: boolean;
    onSelect: (ref: string) => void;
    onDelete?: () => void;
}) {
    const [menu, setMenu] = useState(false);
    const compute = computeLabel(machine.compute_units_this_month);
    return (
        <div className={"envrow" + (selected ? " envrow--on" : "")}>
            <button className="envrow__main" aria-current={selected ? "true" : undefined} onClick={() => onSelect(machine.ref)}>
                <span className={"envrow__dot envrow__dot--" + machine.status} aria-hidden="true" />
                <span className="envrow__what">
                    <strong>{machine.display_name || machine.name}</strong>
                    <small>
                        {STATUS_WORD[machine.status] ?? machine.status} · {sizeLabel(machine.size)}
                        {compute && <> · {compute}</>}
                    </small>
                </span>
            </button>
            {onDelete && (
                <span className="envrow__more">
                    <button aria-label={"More for " + machine.name} aria-expanded={menu} aria-haspopup="menu" onClick={() => setMenu((was) => !was)}>
                        <MoreIcon size={16} />
                    </button>
                    {menu && (
                        <div className="envmenu" role="menu" onKeyDown={(event) => { if (event.key === "Escape") setMenu(false); }}>
                            <button role="menuitem" className="envmenu__danger" onClick={() => { setMenu(false); onDelete(); }}>Delete…</button>
                        </div>
                    )}
                </span>
            )}
        </div>
    );
}

const CONNECT_COMMAND = "lemma machine connect";

function CopyButton({ text, label }: { text: string; label: string }) {
    const [copied, setCopied] = useState<"yes" | "no" | null>(null);
    useEffect(() => {
        if (!copied) return;
        const timer = setTimeout(() => setCopied(null), 2_000);
        return () => clearTimeout(timer);
    }, [copied]);
    return (
        <button
            className="envcopy"
            aria-label={label}
            title={copied === "no" ? "Could not copy" : label}
            onClick={() => copyText(text).then(() => setCopied("yes"), () => setCopied("no"))}
        >
            {copied === "yes" ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
        </button>
    );
}

/** How a laptop or server joins: one command, run there. */
function ConnectPanel() {
    return (
        <div className="envconnect">
            <p>Connect a machine of your own by running this on it:</p>
            <div className="envconnect__cmd">
                <code>{CONNECT_COMMAND}</code>
                <CopyButton text={CONNECT_COMMAND} label="Copy the command" />
            </div>
        </div>
    );
}

function CreateMachine({ podId, policy, onClose, onCreated }: {
    podId: string;
    policy: MachinePolicy;
    onClose: () => void;
    onCreated: (ref: string) => void;
}) {
    const offered = policy.templates.length > 0 ? policy.templates : [{ template: "workspace" as MachineTemplate, sizes: [policy.default_size] }];
    const [name, setName] = useState("");
    const [template, setTemplate] = useState<MachineTemplate>(offered[0].template);
    const sizes = offered.find((offer) => offer.template === template)?.sizes ?? [policy.default_size];
    const [size, setSize] = useState(sizes.includes(policy.default_size) ? policy.default_size : sizes[0]);
    const [visibility, setVisibility] = useState<"pod" | "restricted">("pod");
    const create = useCreatePodMachine(podId);
    const nameOk = validMachineName(name);

    const pickTemplate = (next: MachineTemplate) => {
        setTemplate(next);
        const nextSizes = offered.find((offer) => offer.template === next)?.sizes ?? [];
        if (!nextSizes.includes(size)) setSize(nextSizes.includes(policy.default_size) ? policy.default_size : nextSizes[0] ?? policy.default_size);
    };

    return (
        <Modal title="Create pod machine" subtitle="Shared by this space's people and agents" narrow onClose={onClose}>
            <form onSubmit={(event) => {
                event.preventDefault();
                if (!nameOk) return;
                create.mutate({ name, template, size, visibility }, { onSuccess: (made) => onCreated(made.ref) });
            }}>
                <div className="field">
                    <label htmlFor="env-name">Name</label>
                    <input id="env-name" value={name} autoComplete="off" spellCheck={false} placeholder="build-box"
                        onChange={(event) => setName(event.target.value.toLowerCase())} />
                    {name && !nameOk && <small className="envform__hint">Lower-case letters, digits and dashes, up to 40.</small>}
                </div>
                <div className="field">
                    <label htmlFor="env-template">Template</label>
                    <select id="env-template" value={template} onChange={(event) => pickTemplate(event.target.value as MachineTemplate)}>
                        {offered.map((offer) => (
                            <option key={offer.template} value={offer.template}>
                                {offer.template === "workspace-docker" ? "Workspace with Docker" : "Workspace"}
                            </option>
                        ))}
                    </select>
                </div>
                <div className="field">
                    <label htmlFor="env-size">Size</label>
                    <select id="env-size" value={size} onChange={(event) => setSize(event.target.value)}>
                        {sizes.map((offer) => <option key={offer} value={offer}>{sizeLabel(offer)}</option>)}
                    </select>
                </div>
                <div className="field">
                    <label htmlFor="env-visibility">Who can use it</label>
                    <select id="env-visibility" value={visibility} onChange={(event) => setVisibility(event.target.value as "pod" | "restricted")}>
                        <option value="pod">Everyone in this space</option>
                        <option value="restricted">Only the people it is granted to</option>
                    </select>
                </div>
                {create.isError && <p className="envform__error" role="alert">{problemText(create.error, "It could not be created.")}</p>}
                <div className="modal__acts">
                    <button type="button" className="linkish" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn--primary" disabled={!nameOk || create.isPending}>
                        {create.isPending ? "Creating…" : "Create"}
                    </button>
                </div>
            </form>
        </Modal>
    );
}

function DeleteMachine({ podId, machine, onClose, onDeleted }: {
    podId: string;
    machine: Machine;
    onClose: () => void;
    onDeleted: () => void;
}) {
    const remove = useDeletePodMachine(podId);
    return (
        <Modal title={"Delete " + (machine.display_name || machine.name) + "?"} narrow onClose={onClose}>
            <p className="envform__lead">
                Its disk, processes and previews go with it, and agents working on it lose it mid-run.
                This cannot be undone.
            </p>
            {remove.isError && <p className="envform__error" role="alert">{problemText(remove.error, "It could not be deleted.")}</p>}
            <div className="modal__acts">
                <button className="linkish" onClick={onClose}>Keep it</button>
                <button className="btn btn--danger" disabled={remove.isPending} onClick={() => remove.mutate(machine.name, { onSuccess: onDeleted })}>
                    {remove.isPending ? "Deleting…" : "Delete"}
                </button>
            </div>
        </Modal>
    );
}

/* ── clean up ─────────────────────────────────────────────────────── */

/** The menu in the bar: the light clean-ups together, and resetting the
 *  disk apart from them behind its own typed confirmation. */
export function CleanUp({ machine, podId }: { machine: Machine; podId: string }) {
    const [open, setOpen] = useState<"menu" | "clean" | "home" | null>(null);
    return (
        <span className="envclean">
            <button className="cbar__button" aria-haspopup="menu" aria-expanded={open === "menu"} onClick={() => setOpen((was) => was === "menu" ? null : "menu")}>
                Clean up
            </button>
            {open === "menu" && (
                <div className="envmenu envmenu--right" role="menu" onKeyDown={(event) => { if (event.key === "Escape") setOpen(null); }}>
                    <button role="menuitem" onClick={() => setOpen("clean")}>Free up space…</button>
                    <button role="menuitem" className="envmenu__danger" onClick={() => setOpen("home")}>Reset home…</button>
                </div>
            )}
            {open === "clean" && <CleanDialog machine={machine} podId={podId} onClose={() => setOpen(null)} />}
            {open === "home" && <ResetHomeDialog machine={machine} podId={podId} onClose={() => setOpen(null)} />}
        </span>
    );
}

function CleanDialog({ machine, podId, onClose }: { machine: Machine; podId: string; onClose: () => void }) {
    const offered = cleanupScopes(machine.capabilities);
    const [picked, setPicked] = useState<Set<ResetScope>>(() => new Set(offered.filter((scope) => scope !== "processes")));
    const reset = useResetMachine(machine.ref, podId);
    const results: ResetResult[] | null = reset.data?.results ?? null;
    const toggle = (scope: ResetScope) => setPicked((was) => {
        const next = new Set(was);
        if (next.has(scope)) next.delete(scope); else next.add(scope);
        return next;
    });

    return (
        <Modal title={"Clean up " + (machine.display_name || machine.name)} narrow onClose={onClose}>
            {results ? (
                <div className="envresult" role="status">
                    <p className="envform__lead">
                        Done{totalFreed(results) > 0 ? ` — ${readableSize(totalFreed(results))} freed` : ""}.
                    </p>
                    <ul>{resetLines(results).map((line) => <li key={line}>{line}</li>)}</ul>
                    <div className="modal__acts"><button className="btn btn--primary" onClick={onClose}>Close</button></div>
                </div>
            ) : (
                <>
                    {offered.map((scope) => (
                        <label className="check" key={scope}>
                            <input type="checkbox" checked={picked.has(scope)} onChange={() => toggle(scope)} />
                            <span>{SCOPE_WORDS[scope].label}<em>{SCOPE_WORDS[scope].note}</em></span>
                        </label>
                    ))}
                    {reset.isError && <p className="envform__error" role="alert">{problemText(reset.error, "That did not finish.")}</p>}
                    <div className="modal__acts">
                        <button className="linkish" onClick={onClose}>Cancel</button>
                        <button className="btn btn--primary" disabled={picked.size === 0 || reset.isPending}
                            onClick={() => reset.mutate({ scopes: offered.filter((scope) => picked.has(scope)) })}>
                            {reset.isPending ? "Cleaning…" : "Clean up"}
                        </button>
                    </div>
                </>
            )}
        </Modal>
    );
}

function ResetHomeDialog({ machine, podId, onClose }: { machine: Machine; podId: string; onClose: () => void }) {
    const [typed, setTyped] = useState("");
    const [forgetLogins, setForgetLogins] = useState(false);
    const reset = useResetMachine(machine.ref, podId);
    const word = machine.name;
    const results = reset.data?.results ?? null;

    return (
        <Modal title="Reset home" subtitle={machine.display_name || machine.name} narrow onClose={onClose}>
            {results ? (
                <div className="envresult" role="status">
                    <p className="envform__lead">The home folder was replaced with a fresh disk.</p>
                    <ul>{resetLines(results).map((line) => <li key={line}>{line}</li>)}</ul>
                    <div className="modal__acts"><button className="btn btn--primary" onClick={onClose}>Close</button></div>
                </div>
            ) : (
                <>
                    <p className="envform__lead">
                        Everything in <code>{machine.home}</code> is deleted: files, checkouts, installed tools and
                        history. Running processes stop. This cannot be undone.
                    </p>
                    <label className="check">
                        <input type="checkbox" checked={forgetLogins} onChange={(event) => setForgetLogins(event.target.checked)} />
                        <span>
                            Also forget logins
                            <em>Off, the browser&apos;s sign-ins are copied onto the new disk, so sites stay signed in.</em>
                        </span>
                    </label>
                    <div className="field">
                        <label htmlFor="env-reset-confirm">Type <strong>{word}</strong> to confirm</label>
                        <input id="env-reset-confirm" value={typed} autoComplete="off" spellCheck={false} onChange={(event) => setTyped(event.target.value)} />
                    </div>
                    {reset.isError && <p className="envform__error" role="alert">{problemText(reset.error, "The reset did not finish.")}</p>}
                    <div className="modal__acts">
                        <button className="linkish" onClick={onClose}>Cancel</button>
                        <button className="btn btn--danger" disabled={typed !== word || reset.isPending}
                            onClick={() => reset.mutate({ scopes: ["home"], confirm: true, forget_logins: forgetLogins })}>
                            {reset.isPending ? "Resetting…" : "Reset home"}
                        </button>
                    </div>
                </>
            )}
        </Modal>
    );
}

/* ── the dock ─────────────────────────────────────────────────────── */

export type DockTab = "terminal" | "processes" | "previews" | "files" | "logins";

const TABS: { id: DockTab; label: string; needs: keyof Machine["capabilities"] }[] = [
    { id: "terminal", label: "Terminal", needs: "pty" },
    { id: "processes", label: "Processes", needs: "processes" },
    { id: "previews", label: "Previews", needs: "previews" },
    { id: "files", label: "Files", needs: "files" },
    { id: "logins", label: "Logins", needs: "browser" },
];

/** Tools for the selected machine, under its screen. On a narrow pane it is a
 *  bottom sheet: the tab strip stays, the body opens over the screen. */
export function Dock({ machine, visible, myFiles, open, onOpen }: {
    machine: Machine;
    visible: boolean;
    /** The full file explorer, for your own machine. */
    myFiles: ReactNode;
    open: boolean;
    onOpen: (open: boolean) => void;
}) {
    const [tab, setTab] = useState<DockTab>("files");
    /* The shell is started by opening its tab and then kept, so flicking to
       Processes and back does not start a second one. */
    const [shellOpened, setShellOpened] = useState(false);
    const [shell, setShell] = useState(0);
    const usable = machine.access !== "status";
    const available = (needs: keyof Machine["capabilities"]) => usable && machine.capabilities?.[needs] !== false;

    const pick = (next: DockTab) => {
        setTab(next);
        onOpen(true);
        if (next === "terminal") setShellOpened(true);
    };

    return (
        <section className={"envdock" + (open ? " envdock--open" : "")} aria-label="Machine tools">
            <div className="envdock__tabs" role="tablist">
                {TABS.map((entry) => (
                    <button
                        key={entry.id}
                        role="tab"
                        aria-selected={open && tab === entry.id}
                        disabled={!available(entry.needs)}
                        className="envdock__tab"
                        onClick={() => (open && tab === entry.id ? onOpen(false) : pick(entry.id))}
                    >
                        {entry.label}
                    </button>
                ))}
                <span className="cbar__spacer" />
                <button className="envdock__toggle" aria-expanded={open} onClick={() => onOpen(!open)}>
                    {open ? "Hide" : "Show"}
                </button>
            </div>
            {!usable && open && (
                <p className="computer-note">You can see this machine&apos;s status, but not use it. Ask a manager of this space for access.</p>
            )}
            {usable && (
                <div className="envdock__body" hidden={!open}>
                    {shellOpened && available("pty") && (
                        <div className="envdock__panel" hidden={tab !== "terminal"}>
                            <Terminal key={machine.ref} machineRef={machine.ref} session={shell} onRestart={() => setShell((was) => was + 1)} />
                        </div>
                    )}
                    {tab === "processes" && <Processes machine={machine} visible={visible && open} />}
                    {tab === "previews" && <Previews machine={machine} visible={visible && open} />}
                    {tab === "files" && (machine.kind === "default" ? myFiles : <MachineFiles machine={machine} visible={visible && open} />)}
                    {tab === "logins" && <MachineLogins machine={machine} visible={visible && open} />}
                </div>
            )}
        </section>
    );
}

function Processes({ machine, visible }: { machine: Machine; visible: boolean }) {
    const processes = useProcesses(machine.ref, visible);
    const kill = useKillProcess(machine.ref);
    const items = processes.data ?? [];
    return (
        <div className="envlist">
            {processes.isPending && <p className="computer-note" role="status">Listing processes…</p>}
            {processes.isError && (
                <p className="computer-note" role="alert">
                    The processes could not be listed.{" "}
                    <button className="computer-inline" onClick={() => void processes.refetch()}>Try again</button>
                </p>
            )}
            {!processes.isPending && !processes.isError && items.length === 0 && (
                <p className="computer-note">Nothing is running. Commands agents start show up here.</p>
            )}
            {items.map((process) => (
                <div className="envitem" key={process.id}>
                    <span className="envitem__what">
                        <code>{process.command}</code>
                        <small>
                            {process.state}{process.exit_code !== null && process.exit_code !== undefined ? ` (${process.exit_code})` : ""}
                            {process.service && <span className="envbadge">service: {process.service}</span>}
                            {process.run_id && <> · run {process.run_id}</>}
                            {process.tty && <> · terminal</>}
                        </small>
                    </span>
                    {!isFinished(process.state) && (
                        <button className="computer-inline" disabled={kill.isPending && kill.variables === process.id}
                            onClick={() => kill.mutate(process.id)}>
                            {kill.isPending && kill.variables === process.id ? "Stopping…" : "Kill"}
                        </button>
                    )}
                </div>
            ))}
            {kill.isError && <p className="computer-note" role="alert">{problemText(kill.error, "That process could not be stopped.")}</p>}
        </div>
    );
}

function Previews({ machine, visible }: { machine: Machine; visible: boolean }) {
    const previews = usePreviews(machine.ref, visible);
    const create = useCreatePreview(machine.ref);
    const revoke = useRevokePreview(machine.ref);
    const [port, setPort] = useState("3000");
    const [visibility, setVisibility] = useState<"private" | "link">("private");
    const [ttl, setTtl] = useState(LINK_TTLS[0].seconds);
    const portNumber = Number(port);
    const portOk = Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535;
    const items = (previews.data ?? []).filter((preview) => previewLive(preview));

    return (
        <div className="envlist">
            <form className="envpreview__form" onSubmit={(event) => {
                event.preventDefault();
                if (!portOk) return;
                create.mutate({ port: portNumber, visibility, ttl_seconds: visibility === "link" ? ttl : undefined });
            }}>
                <label>
                    <span>Port</span>
                    <input inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value.replace(/\D/g, ""))} aria-invalid={!portOk} />
                </label>
                <label>
                    <span>Who can open it</span>
                    <select value={visibility} onChange={(event) => setVisibility(event.target.value as "private" | "link")}>
                        <option value="private">People who can see this machine</option>
                        <option value="link">Anyone with the link</option>
                    </select>
                </label>
                {visibility === "link" && (
                    <label>
                        <span>For</span>
                        <select value={ttl} onChange={(event) => setTtl(Number(event.target.value))}>
                            {LINK_TTLS.map((choice) => <option key={choice.seconds} value={choice.seconds}>{choice.label}</option>)}
                        </select>
                    </label>
                )}
                <button className="btn btn--primary" type="submit" disabled={!portOk || create.isPending}>
                    {create.isPending ? "Opening…" : "Preview"}
                </button>
            </form>
            {create.isError && <p className="computer-note" role="alert">{problemText(create.error, "That preview could not be made.")}</p>}
            {previews.isPending && <p className="computer-note" role="status">Listing previews…</p>}
            {previews.isError && (
                <p className="computer-note" role="alert">
                    The previews could not be listed.{" "}
                    <button className="computer-inline" onClick={() => void previews.refetch()}>Try again</button>
                </p>
            )}
            {!previews.isPending && !previews.isError && items.length === 0 && (
                <p className="computer-note">Nothing shared. Start a server on the machine, then preview its port.</p>
            )}
            {items.map((preview) => (
                <div className="envitem" key={preview.id}>
                    <span className="envitem__what">
                        <strong>Port {preview.port}</strong>
                        <small>
                            {preview.visibility === "link" ? "Anyone with the link" : "Signed-in people who can see it"}
                            {preview.expires_at && <> · until {new Date(preview.expires_at).toLocaleString(undefined, { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" })}</>}
                        </small>
                    </span>
                    <a className="computer-inline" href={preview.url} target="_blank" rel="noreferrer"><ExternalIcon size={14} /> Open</a>
                    <CopyButton text={preview.url} label="Copy the link" />
                    <button className="computer-inline" disabled={revoke.isPending && revoke.variables === preview.id} onClick={() => revoke.mutate(preview.id)}>
                        Revoke
                    </button>
                </div>
            ))}
            {revoke.isError && <p className="computer-note" role="alert">{problemText(revoke.error, "That preview could not be revoked.")}</p>}
        </div>
    );
}

/** A read-only walk through another machine's files. Your own machine gets
 *  the full explorer instead. */
function MachineFiles({ machine, visible }: { machine: Machine; visible: boolean }) {
    const [path, setPath] = useState<string | null>(null);
    const [wake, setWake] = useState(false);
    const listing = useMachineFiles(machine.ref, path, wake, visible);
    const data = listing.data;
    const here = data?.path ?? path ?? "";
    const home = data?.home_root ?? machine.home;
    const up = here && here !== home && here.startsWith(home) ? here.slice(0, here.lastIndexOf("/")) || home : null;
    const entries = [...(data?.entries ?? [])].sort((a, b) =>
        (a.kind === "directory" ? 0 : 1) - (b.kind === "directory" ? 0 : 1) || a.name.localeCompare(b.name));

    return (
        <div className="envlist">
            <div className="envfiles__path">
                {up && <button className="computer-inline" onClick={() => setPath(up)}>Up</button>}
                <code>{here}</code>
                <button className="computer-again" aria-label="Refresh" title="Refresh" disabled={listing.isFetching} onClick={() => void listing.refetch()}>
                    <RefreshIcon size={15} />
                </button>
            </div>
            {listing.isPending && <p className="computer-note" role="status">Listing files…</p>}
            {listing.isError && (
                <p className="computer-note" role="alert">
                    This machine could not be read.{" "}
                    <button className="computer-inline" onClick={() => void listing.refetch()}>Try again</button>
                </p>
            )}
            {data?.sleeping && (
                <p className="computer-note">
                    This machine is asleep.{" "}
                    <button className="computer-inline" onClick={() => setWake(true)}>Wake it</button>
                </p>
            )}
            {data && !data.sleeping && entries.length === 0 && <p className="computer-note">This folder is empty.</p>}
            {entries.map((entry) => entry.kind === "directory" ? (
                <button className="envitem envitem--button" key={entry.path} onClick={() => setPath(entry.path)}>
                    <FolderIcon size={17} /> <span className="envitem__what"><strong>{entry.name}</strong></span>
                    <ChevronRightIcon size={14} />
                </button>
            ) : (
                <div className="envitem" key={entry.path}>
                    <FileIcon size={17} />
                    <span className="envitem__what"><strong>{entry.name}</strong><small>{readableSize(entry.size_bytes)}</small></span>
                </div>
            ))}
        </div>
    );
}

function MachineLogins({ machine, visible }: { machine: Machine; visible: boolean }) {
    const [wake, setWake] = useState(false);
    const [removing, setRemoving] = useState<WebLogin | null>(null);
    const logins = useMachineLogins(machine.ref, wake, visible);
    const forget = useForgetMachineLogin(machine.ref);
    const items = logins.data?.items ?? [];
    const sleeping = logins.data?.sleeping === true;
    const sites = groupSites(items);
    const shown = sites.split ? [...sites.signedIn, ...sites.other] : items;

    return (
        <div className="envlist">
            {logins.isPending && <p className="computer-note" role="status">Asking the browser…</p>}
            {logins.isError && (
                <p className="computer-note" role="alert">
                    The browser could not be asked just now.{" "}
                    <button className="computer-inline" onClick={() => void logins.refetch()}>Try again</button>
                </p>
            )}
            {sleeping && (
                <p className="computer-note">
                    This machine is asleep, and its browser is the only thing that knows.{" "}
                    <button className="computer-inline" onClick={() => setWake(true)}>Wake it and ask</button>
                </p>
            )}
            {!sleeping && !logins.isPending && !logins.isError && items.length === 0 && (
                <p className="computer-note">No sites yet. The browser keeps a session once somebody signs in.</p>
            )}
            {!sleeping && shown.map((login) => (
                <div className="envitem" key={login.site}>
                    {saidSoFor(login) ? <LockIcon size={16} /> : <GlobeIcon size={16} />}
                    <span className="envitem__what"><strong>{login.site}</strong><small>{loginNote(login, new Date())}</small></span>
                    <button className="computer-inline" onClick={() => setRemoving(login)}>Remove</button>
                </div>
            ))}
            {removing && (
                <Modal title={"Remove " + removing.site + "?"} narrow onClose={() => { forget.reset(); setRemoving(null); }}>
                    <p className="envform__lead">
                        This signs the browser out of <strong>{removing.site}</strong>. Agents working there will
                        need somebody to sign in again.
                    </p>
                    {forget.isError && (
                        <p className="envform__error" role="alert">
                            That did not sign out. It needs the machine running, because it really signs the browser out.
                        </p>
                    )}
                    <div className="modal__acts">
                        <button className="linkish" onClick={() => { forget.reset(); setRemoving(null); }}>Keep it</button>
                        <button className="btn btn--danger" disabled={forget.isPending}
                            onClick={() => forget.mutate(removing.site, { onSuccess: () => setRemoving(null) })}>
                            {forget.isPending ? "Signing out…" : "Sign out"}
                        </button>
                    </div>
                </Modal>
            )}
        </div>
    );
}

/** What stands in the screen's place for a machine whose browser this pane
 *  does not show. */
export function OtherScreen({ machine }: { machine: Machine }) {
    return (
        <div className="computer-empty envscreen">
            {machine.kind === "connected" ? <ComputerIcon size={26} /> : <TerminalIcon size={26} />}
            <p>
                <strong>{machine.display_name || machine.name}</strong> is {(STATUS_WORD[machine.status] ?? machine.status).toLowerCase()}.
                {machine.capabilities?.browser
                    ? " Its browser is not shown here yet — use the terminal, processes and previews below."
                    : " It has no browser — use the terminal, processes and previews below."}
            </p>
        </div>
    );
}
