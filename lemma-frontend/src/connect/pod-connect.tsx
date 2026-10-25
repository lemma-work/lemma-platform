"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Connector, type ConnectorAccount, type Pod, type PodConnector } from "@/data";
import { askableOfMe, bannerLine, missingForMe, type MyAccount } from "@/data/pod-connectors";
import { CONNECTOR_USE } from "@/data/agents";
import { isLandingPreview } from "@/marketing/preview-mode";
import { useMe } from "@/session/use-me";
import { CloseIcon, PlugIcon } from "@/ui/icons";
import { LoadingIndicator } from "@/ui/loading";
import { ConnectDialog } from "./connect-dialog";
import { connectorProblem, type CatalogEntry, type Install } from "./install";
import { completionPath, hereWith, openAuthorization, useConnectOutcome } from "./round-trip";

/** My organization accounts, in the shape the connections page reads. */
export function asMine(accounts: ConnectorAccount[]): MyAccount[] {
    return accounts.map((one) => ({
        id: one.id, authConfigId: one.authConfigId, connectorId: one.connectorId, label: one.label,
        status: one.status, usable: one.usable, isDefault: one.isDefault,
    }));
}

/** Whether I hold a pod-level permission here, from `podPermissions.me`
 *  under the key every other About part asks it by. The sample says yes —
 *  it has no server to ask and its edits are real, in memory — and the
 *  landing's preview says no: nothing on it is for changing. */
export function usePodMay(podId: string, action: string, asked = true): { allowed: boolean; answered: boolean } {
    const preview = isLandingPreview();
    const permissions = useQuery({
        queryKey: ["pod-actions", podId],
        queryFn: async () => ({ actions: await source.podActions(podId) }),
        enabled: !preview && asked,
        staleTime: 60_000,
    });
    if (preview) return { allowed: false, answered: true };
    return { allowed: (permissions.data?.actions ?? []).includes(action), answered: permissions.isSuccess };
}

/** Everything a pod's connections are read from, once, under shared keys. */
export function useConnectionsData(pod: Pod) {
    const connectors = useQuery({ queryKey: ["pod-connectors", pod.id], queryFn: () => source.listPodConnectors(pod.id), staleTime: 30_000 });
    const accounts = useQuery({ queryKey: ["accounts", pod.orgId], queryFn: () => source.listAccounts(pod.orgId), staleTime: 30_000 });
    const catalog = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors(), staleTime: 10 * 60_000 });
    /* Under the key the organization's Connectors page keeps them by. A
       member who may not list them still connects, against the installation
       the connector names. */
    const installs = useQuery({ queryKey: ["installs", pod.orgId], queryFn: () => source.listInstallations(pod.orgId), staleTime: 60_000, retry: false });
    const titles = useMemo(() => new Map((catalog.data ?? []).map((one) => [one.id, one])), [catalog.data]);
    const mine = useMemo(() => asMine(accounts.data ?? []), [accounts.data]);
    return { connectors, accounts, catalog, installs, titles, mine };
}

/** Starting the connect flow for one pod connector, and coming back.
 *
 *  The same round trip the organization's Connectors page runs — the
 *  provider's page in a tab of its own, the outcome heard here — returning to
 *  the address the person is on now, so a banner over an app comes back to the
 *  app and the Connections page to the connector it was opened on. A
 *  connector whose installation takes a credential rather than a sign-in
 *  opens the same dialog the organization's page does. */
export function useConnectFlow(pod: Pod, titles: Map<string, Connector>, installs: Install[] = []) {
    const cache = useQueryClient();
    const [dialog, setDialog] = useState<{ connector: PodConnector; entry: CatalogEntry; install: Install | null } | null>(null);
    const [waiting, setWaiting] = useState<string | null>(null);
    const [problem, setProblem] = useState<{ slug: string; text: string } | null>(null);

    const refresh = useCallback(() => {
        void cache.invalidateQueries({ queryKey: ["accounts"] });
        void cache.invalidateQueries({ queryKey: ["pod-accounts", pod.id] });
        void cache.invalidateQueries({ queryKey: ["pod-connectors", pod.id] });
    }, [cache, pod.id]);
    useConnectOutcome(() => { setWaiting(null); refresh(); }, refresh);

    const start = useMutation({
        mutationFn: ({ connector, fields }: { connector: PodConnector; fields?: Record<string, unknown> }) =>
            source.startAccount(pod.orgId, connector.connectorId, connector.authConfigId || undefined, completionPath(hereWith()), fields),
        onSuccess: (started, { connector }) => {
            setDialog(null);
            if (!started.authorizeUrl) {
                setProblem({ slug: connector.slug, text: "This one offered no way to sign in. Ask whoever set it up for the organization." });
                return;
            }
            const title = titles.get(connector.connectorId)?.title ?? connector.slug;
            if (openAuthorization(started.authorizeUrl, title)) setWaiting(connector.slug);
            else window.location.assign(started.authorizeUrl);
        },
        onError: (error, { connector }) => setProblem({ slug: connector.slug, text: connectorProblem(error, "That could not be started.") }),
    });

    /** Connect, or with `reconnect` sign in again on the same installation. */
    const connect = (connector: PodConnector, reconnect = false) => {
        setProblem(null);
        const entry = titles.get(connector.connectorId);
        /* Signing in again is the same consent page whatever the kind, and so
           is a connector this page cannot read the kinds of. */
        if (reconnect || !entry) { start.mutate({ connector }); return; }
        setDialog({
            connector,
            entry: { id: entry.id, title: entry.title, description: entry.description, icon: entry.icon, kinds: entry.kinds },
            install: installs.find((one) => one.id === connector.authConfigId)
                ?? { id: connector.authConfigId, connector_id: connector.connectorId, kind: "", name: connector.installationName || connector.authConfigId, status: "ACTIVE" },
        });
    };

    const element: ReactNode = dialog && (
        <ConnectDialog
            orgId={pod.orgId}
            connector={dialog.entry}
            install={dialog.install}
            takenNames={[]}
            mayInstall={false}
            authorizing={start.isPending}
            authorizeFailure={problem?.slug === dialog.connector.slug ? problem.text : null}
            onClose={() => setDialog(null)}
            onDone={() => { setDialog(null); refresh(); }}
            onAuthorize={(_installId, fields) => start.mutate({ connector: dialog.connector, fields })}
        />
    );

    return {
        connect,
        element,
        /** The slug whose sign-in is being fetched, or whose provider tab is open. */
        busy: start.isPending ? start.variables?.connector.slug ?? null : null,
        waiting,
        problem,
        refresh,
    };
}

/** How long the banner trusts what it read. It is drawn on every pod open,
 *  so it reads from the cache under the keys every other part shares, and
 *  reads again only when the window comes back into focus or after a connect
 *  (the flow's `refresh` invalidates both) — never because a pod was opened. */
const BANNER_FRESH = 10 * 60_000;
const BANNER_READS = { staleTime: BANNER_FRESH, refetchOnMount: false, refetchOnWindowFocus: "always" } as const;

/** The line over a pod, or an app in it, when a connector the pod requires
 *  of everyone has no working account of mine: "Kit uses Gmail — connect
 *  yours". One button per connector, and the way it came back is this same
 *  place. Dismissed for the visit, never for good: it is a fact about the
 *  pod, not a notification.
 *
 *  Only for someone who could use the connector once connected — never a
 *  viewer, never somebody a RESTRICTED connector leaves out. And it reads
 *  only what it needs: the pod's connectors first, my accounts only when one
 *  of them could ask me, the catalogue and installations only when it does. */
export function ConnectBanner({ pod }: { pod: Pod }) {
    const preview = isLandingPreview();
    const me = useMe() ?? "";
    const connectors = useQuery({
        queryKey: ["pod-connectors", pod.id], queryFn: () => source.listPodConnectors(pod.id), enabled: !preview, ...BANNER_READS,
    });
    const list = connectors.data ?? [];
    /* Asked only of a server that does not say `can_use` itself. */
    const unsaid = list.some((one) => one.required && one.mode === "PER_MEMBER" && one.canUse === null);
    const podUse = usePodMay(pod.id, CONNECTOR_USE, unsaid);
    const podMayUse = podUse.answered ? podUse.allowed : null;
    const askable = askableOfMe(list, podMayUse);
    const accounts = useQuery({
        queryKey: ["accounts", pod.orgId], queryFn: () => source.listAccounts(pod.orgId), enabled: !preview && askable.length > 0, ...BANNER_READS,
    });
    const mine = useMemo(() => asMine(accounts.data ?? []), [accounts.data]);
    const missing = accounts.isSuccess ? missingForMe(askable, mine, me, podMayUse) : [];
    const catalog = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors(), enabled: missing.length > 0, staleTime: 10 * 60_000 });
    const installs = useQuery({
        queryKey: ["installs", pod.orgId], queryFn: () => source.listInstallations(pod.orgId), enabled: missing.length > 0, staleTime: 60_000, retry: false,
    });
    const titles = useMemo(() => new Map((catalog.data ?? []).map((one) => [one.id, one])), [catalog.data]);
    const flow = useConnectFlow(pod, titles, installs.data);
    const [dismissed, setDismissed] = useState(false);
    if (preview || dismissed || missing.length === 0) return null;
    const name = (id: string) => titles.get(id)?.title ?? "";
    return (
        <aside className="connectbanner" role="status" aria-label="Connections this teammate needs">
            <PlugIcon size={16} aria-hidden="true" />
            <span className="connectbanner__say">
                {bannerLine(missing, name, pod.name)}
                {flow.problem && <small role="alert">{flow.problem.text}</small>}
                {flow.waiting && <small>Finish in the other tab…</small>}
            </span>
            <span className="connectbanner__acts">
                {missing.map(({ connector, status }) => (
                    <button key={connector.slug} className="btn btn--primary" disabled={flow.busy === connector.slug}
                        onClick={() => flow.connect(connector, status.standing === "reconnect")}>
                        {flow.busy === connector.slug
                            ? <LoadingIndicator inline label="Opening sign-in" />
                            : (status.standing === "reconnect" ? "Reconnect " : "Connect ") + (name(connector.connectorId) || connector.slug)}
                    </button>
                ))}
            </span>
            <button className="icon-button connectbanner__close" aria-label="Not now" title="Not now" onClick={() => setDismissed(true)}>
                <CloseIcon size={14} />
            </button>
            {flow.element}
        </aside>
    );
}
