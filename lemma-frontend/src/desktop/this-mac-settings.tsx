"use client";

import "@/styles/desktop.css";
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { ComputerIcon, DownloadIcon, RefreshIcon, TerminalIcon, WarningIcon } from "@/ui/icons";
import { useDesktopBridge } from "./bridge";
import { openSettings } from "./open-settings";
import { capitalised, useThisComputer } from "./this-computer";
import { ThisComputerCard } from "./this-computer-card";
import { readStatus, useAgentHost } from "./agent-host";
import {
    channelLine, friendlyError, healthLine, healthState, hostExecutionRow, onLocalWorkspaceOrigin,
    sandboxWording, updateOffer, sharingBusy, thisMac, thisMacAvailability,
    type Installation, type ThisMacAvailability, type ThisMacSnapshot,
} from "./this-mac";
import { ThisMacSharing } from "./this-mac-sharing";
import { ThisMacAdvanced } from "./this-mac-advanced";

/** Settings → This Mac: the settings a person changes about their own
 *  computer, in the same Settings as everything else.
 *
 *  They used to be a separate window with its own look and its own words —
 *  "System AI profile", "Channels", environment names — reached from a menu
 *  most people never opened. What is left of that window is what has to work
 *  when this page cannot load: health, recovery and diagnostics.
 *
 *  Shown only in the app, on a local install, to its owner. The shell checks
 *  who is calling on every command anyway (`workspace_settings.rs`); the gate
 *  here is so nobody else is shown a machine's settings they cannot use. */

export type ThisMacSection = "this-mac" | "this-mac-agents" | "this-mac-sharing" | "this-mac-updates" | "this-mac-advanced";

export const THIS_MAC_SECTIONS: readonly ThisMacSection[] = [
    "this-mac", "this-mac-agents", "this-mac-sharing", "this-mac-updates", "this-mac-advanced",
];

export function isThisMacSection(section: string): section is ThisMacSection {
    return (THIS_MAC_SECTIONS as readonly string[]).includes(section);
}

/** What this installation is, and whether you own it. Asked once a session:
 *  neither answer changes while the page is open. */
export function useInstallation(enabled: boolean) {
    return useQuery({
        queryKey: ["installation"],
        queryFn: async () => (await lemma().users.installation()) as Installation,
        enabled,
        staleTime: Infinity,
        retry: 1,
    });
}

export function useThisMacAvailability(): ThisMacAvailability {
    const bridge = useDesktopBridge();
    const installation = useInstallation(bridge);
    /* Read after mount: the server has no hostname to compare, and a group
       that appears one commit later is better than one the server invented. */
    const [localOrigin, setLocalOrigin] = useState(false);
    useEffect(() => setLocalOrigin(onLocalWorkspaceOrigin()), []);
    return thisMacAvailability({
        bridge,
        localOrigin,
        installation: installation.data,
        loading: installation.isPending && bridge,
    });
}

/** The daemon's picture of this installation. Polled quickly while sharing is
 *  changing — the enable returns at its first progress report and carries on
 *  — and slowly otherwise, so health on the Overview stays true. */
export function useThisMacSnapshot() {
    return useQuery({
        queryKey: ["this-mac"],
        queryFn: () => thisMac.snapshot(),
        refetchInterval: (query) => (sharingBusy(query.state.data?.sharing ?? null) ? 1_500 : 15_000),
        retry: 1,
    });
}

function Loading({ snapshot, children }: { snapshot: ReturnType<typeof useThisMacSnapshot>; children: (data: ThisMacSnapshot) => ReactNode }) {
    if (snapshot.isPending) return <p className="empty-row" role="status">Reading this computer’s settings…</p>;
    if (snapshot.isError) {
        return (
            <div className="thismac-problem" role="alert">
                <p>{friendlyError(snapshot.error)}</p>
                <button className="btn" onClick={() => void snapshot.refetch()}><RefreshIcon size={13} /> Try again</button>
            </div>
        );
    }
    return <>{children(snapshot.data)}</>;
}

/** One setting: its name, its one line of consequence, and its control. */
export function SettingRow({ name, consequence, children, id }: { name: string; consequence?: ReactNode; children?: ReactNode; id?: string }) {
    return (
        <div className="thismac-row" id={id}>
            <div className="thismac-row__text">
                <span className="thismac-row__name">{name}</span>
                {consequence && <span className="thismac-row__said">{consequence}</span>}
            </div>
            {children && <div className="thismac-row__control">{children}</div>}
        </div>
    );
}

/* ── overview ──────────────────────────────────────────────────────── */

function Overview() {
    const noun = useThisComputer();
    const queryClient = useQueryClient();
    const snapshot = useThisMacSnapshot();
    const update = useQuery({ queryKey: ["this-mac-update"], queryFn: () => thisMac.checkUpdate(), staleTime: 10 * 60_000, retry: 0 });
    const [said, setSaid] = useState<string | null>(null);

    const login = useMutation({
        mutationFn: (enabled: boolean) => thisMac.setStartAtLogin(enabled),
        onSuccess: (enabled) => queryClient.setQueryData<ThisMacSnapshot>(["this-mac"], (was) => was && { ...was, app: { ...was.app, start_at_login: enabled } }),
        onError: (problem) => setSaid(friendlyError(problem)),
    });
    const repair = useMutation({
        mutationFn: () => thisMac.repair(),
        onSuccess: (ran) => setSaid(ran ? "Lemma is checking its files and will start again in a moment." : null),
        onError: (problem) => setSaid(friendlyError(problem)),
    });
    const logs = useMutation({ mutationFn: () => thisMac.openLogs(), onError: (problem) => setSaid(friendlyError(problem)) });

    return (
        <Loading snapshot={snapshot}>
            {(data) => {
                const state = healthState(data);
                return (
                    <div className="thismac">
                        <p className={"thismac-health thismac-health--" + state} role="status">
                            <i aria-hidden="true" />
                            {healthLine(data, update.data ?? null)}
                        </p>
                        <SettingRow name="Start at login" consequence={`Lemma opens when you sign in to ${noun}, so teammates and channels keep answering.`}>
                            <input
                                type="checkbox"
                                className="thismac-switch"
                                role="switch"
                                aria-label="Start at login"
                                checked={data.app.start_at_login}
                                disabled={login.isPending}
                                onChange={(event) => { setSaid(null); login.mutate(event.target.checked); }}
                            />
                        </SettingRow>
                        <SettingRow name="Repair" consequence="Checks Lemma’s own files and replaces damaged ones. Your pods, files and accounts are not touched.">
                            <button className="btn" disabled={repair.isPending} onClick={() => { setSaid(null); repair.mutate(); }}>
                                {repair.isPending ? "Repairing…" : "Verify & repair"}
                            </button>
                        </SettingRow>
                        <SettingRow name="Logs" consequence="What Lemma wrote while it ran, for when something needs explaining.">
                            <button className="linkish" onClick={() => { setSaid(null); logs.mutate(); }}><TerminalIcon size={13} /> Open logs</button>
                        </SettingRow>
                        {said && <p className="thismac-said" role="status">{said}</p>}
                        <p className="thismac-foot">
                            Erasing data and restarting into Recovery stay in the menu bar: Lemma → Recovery…
                        </p>
                    </div>
                );
            }}
        </Loading>
    );
}

/* ── coding agents ─────────────────────────────────────────────────── */

function CodingAgents() {
    const noun = useThisComputer();
    const snapshot = useThisMacSnapshot();
    const queryClient = useQueryClient();
    const [problem, setProblem] = useState<string | null>(null);
    const prepare = useMutation({
        mutationFn: () => thisMac.prepareSandbox(),
        onSuccess: () => {
            queryClient.setQueryData<ThisMacSnapshot>(["this-mac"], (was) => was && { ...was, sandbox_images: { state: "downloading", detail: "" } });
        },
        onError: (cause) => setProblem(friendlyError(cause)),
    });
    return (
        <div className="thismac">
            {/* The same card the Models page leads with, so this computer
                reads the same in both places. Adding its agents for
                teammates to pick is an organization decision and stays on
                Models. */}
            <ThisComputerCard />
            <p className="thismac-foot">
                Choose which of its agents teammates can use in{" "}
                <button className="linkish" onClick={() => openSettings("models")}>Models</button>.
            </p>
            <HostExecution />
            <Loading snapshot={snapshot}>
                {(data) => {
                    const wording = sandboxWording(data.sandbox_images?.state, noun);
                    return (
                        <SettingRow name="Workspace sandbox" consequence={wording.text}>
                            {wording.offer && (
                                <button className="btn" disabled={prepare.isPending} onClick={() => { setProblem(null); prepare.mutate(); }}>
                                    <DownloadIcon size={13} /> {data.sandbox_images?.state === "failed" ? "Try again" : "Download"}
                                </button>
                            )}
                        </SettingRow>
                    );
                }}
            </Loading>
            {problem && <p className="thismac-said thismac-said--bad" role="alert">{problem}</p>}
        </div>
    );
}

/** "Run commands on this Mac". Owner's runs only: the backend keeps every
 *  teammate's run in the VM whatever this says. */
function HostExecution() {
    const host = useAgentHost();
    const [problem, setProblem] = useState<string | null>(null);
    const change = useMutation({
        mutationFn: (enabled: boolean) => thisMac.setHostExecution(enabled),
        /* The shell answers with the host's fresh status; the poll catches up
           with it on its next tick anyway. */
        onSuccess: (answer) => { if (readStatus(answer)) void host.refetch(); },
        onError: (cause) => setProblem(friendlyError(cause)),
    });
    const row = hostExecutionRow(host.status);
    return (
        <>
            <SettingRow name="Run commands on this Mac" consequence={row.blocked ?? row.consequence}>
                <input
                    type="checkbox"
                    className="thismac-switch"
                    role="switch"
                    aria-label="Run commands on this Mac"
                    checked={change.isPending ? change.variables === true : row.checked}
                    disabled={row.blocked !== null || change.isPending}
                    title={row.blocked ?? undefined}
                    onChange={(event) => { setProblem(null); change.mutate(event.target.checked); }}
                />
            </SettingRow>
            {problem && <p className="thismac-said thismac-said--bad" role="alert">{problem}</p>}
        </>
    );
}

/* ── updates ───────────────────────────────────────────────────────── */

function Updates() {
    const snapshot = useThisMacSnapshot();
    const update = useQuery({ queryKey: ["this-mac-update"], queryFn: () => thisMac.checkUpdate(), staleTime: 10 * 60_000, retry: 0 });
    const [problem, setProblem] = useState<string | null>(null);
    const install = useMutation({
        mutationFn: (version: string) => thisMac.installUpdate(version),
        onSuccess: () => void update.refetch(),
        onError: (cause) => setProblem(friendlyError(cause)),
    });
    const status = update.data ?? null;
    const offer = updateOffer(status);
    const channel = snapshot.data?.app.channel ?? status?.channel ?? "dev";
    return (
        <div className="thismac">
            <SettingRow
                name={status ? "Lemma " + status.currentVersion : "Lemma"}
                consequence={update.isFetching ? "Checking for updates…"
                    : update.isError ? "Couldn’t check. " + friendlyError(update.error)
                        : !status ? ""
                            : !status.updatesSupported ? channelLine(status, channel)
                                : status.availableVersion ? `Lemma ${status.availableVersion} is available.` : "Up to date."}
            >
                <button className="btn" disabled={update.isFetching} onClick={() => { setProblem(null); void update.refetch(); }}>
                    <RefreshIcon size={13} className={update.isFetching ? "spin" : undefined} /> Check now
                </button>
            </SettingRow>
            {status?.availableVersion && (
                <SettingRow name={"Install " + status.availableVersion} consequence={offer.blocked ?? offer.cost}>
                    <button
                        className="btn btn--primary"
                        disabled={Boolean(offer.blocked) || install.isPending}
                        /* The version shown, so the shell can refuse if the
                           feed moved on since; it asks natively first. */
                        onClick={() => { setProblem(null); install.mutate(status.availableVersion!); }}
                    >
                        {install.isPending ? "Downloading…" : "Download and install"}
                    </button>
                </SettingRow>
            )}
            <SettingRow name="Channel" consequence={channelLine(status, channel)}>
                <span className="pill">{channel}</span>
            </SettingRow>
            {problem && <p className="thismac-said thismac-said--bad" role="alert">{problem}</p>}
        </div>
    );
}

/* ── the pane ──────────────────────────────────────────────────────── */

/** Said instead of controls when the window is on a shared address. */
function Elsewhere() {
    const noun = capitalised(useThisComputer());
    return (
        <div className="thismac-problem" role="status">
            <p>
                <WarningIcon size={14} /> Lemma is shared right now, and this window is on the shared address.
                {" "}{noun}’s settings open from the menu bar there: Lemma → Desktop settings…, where sharing can be turned off.
            </p>
        </div>
    );
}

export function ThisMacPane({ section, focus }: { section: ThisMacSection; focus?: string | null }) {
    const availability = useThisMacAvailability();
    if (availability === "pending") return <p className="empty-row" role="status">Checking…</p>;
    if (availability === "elsewhere") return <Elsewhere />;
    if (availability === "hidden") return null;
    if (section === "this-mac-agents") return <CodingAgents />;
    if (section === "this-mac-sharing") return <ThisMacSharing />;
    if (section === "this-mac-updates") return <Updates />;
    if (section === "this-mac-advanced") return <ThisMacAdvanced focus={focus ?? null} />;
    return <Overview />;
}

export { ComputerIcon as ThisMacIcon };
