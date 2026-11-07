import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey, useOrg } from "../../../data/hooks";
import { useMyOrgRole } from "../access/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { canManage } from "@web/org/membership";
import type { Field, Values } from "@web/connect/schema";
import { connectorsLine, type MayManage } from "./model";
import { addOwn, saveInstall, submitConnect, type ConnectPlan } from "./plan";
import type { Install, NewInstall, PendingSignIn } from "./source";

const PART = "connectors";

/** This part's reads and writes, through react-query. Key every query with
 *  `adminKey(useCacheKey(), "connectors", …)`. */
export function useConnectorsSource() {
    return useAdminSource().connectors;
}

/** The organization every connector screen is about: the one chosen. */
export function useOrgId(): string | null {
    return useOrg().org?.id ?? null;
}

/** The catalog: catalog data, which changes on a deploy, not on a tap. */
export function useCatalog() {
    const source = useConnectorsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, PART, "catalog"), queryFn: () => source.catalog(), staleTime: 10 * 60_000 });
}

/** One connector with its kinds' schemas and the OAuth redirect URI. */
export function useDetail(connectorId: string) {
    const source = useConnectorsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, PART, "detail", connectorId), queryFn: () => source.detail(connectorId), staleTime: 10 * 60_000, retry: false, enabled: Boolean(connectorId) });
}

export function useAccounts(orgId: string | null) {
    const source = useConnectorsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, PART, "accounts", orgId), queryFn: () => source.accounts(orgId!), enabled: Boolean(orgId), staleTime: 30_000 });
}

export function useInstalls(orgId: string | null) {
    const source = useConnectorsSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, PART, "installs", orgId), queryFn: () => source.installs(orgId!), enabled: Boolean(orgId), staleTime: 60_000, retry: false });
}

/** One install read fresh, for the form that edits it. */
export function useInstallRead(orgId: string | null, install: Install | null) {
    const source = useConnectorsSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: adminKey(key, PART, "install", orgId, install?.id),
        queryFn: () => source.install(orgId!, install!),
        enabled: Boolean(orgId && install),
        staleTime: 0,
        retry: false,
    });
}

/** One connector, with what this organization holds of it: the catalog's
 *  row, your accounts on it, its installs, and every install name in the
 *  organization (names are unique there, so a new one is named apart). */
export function useConnectorHere(connectorId: string) {
    const { org, pending: orgPending } = useOrg();
    const orgId = org?.id ?? null;
    const catalog = useCatalog();
    const accounts = useAccounts(orgId);
    const installs = useInstalls(orgId);
    const all = installs.data ?? [];
    return {
        orgId,
        /** No organization chosen, and none on the way. */
        noOrg: !orgId && !orgPending,
        connector: catalog.data?.find((one) => one.id === connectorId) ?? null,
        accounts: (accounts.data ?? []).filter((one) => one.connectorId === connectorId),
        installs: all.filter((one) => one.connector_id === connectorId),
        takenNames: all.map((one) => one.name),
        /* Installs are read for what they add, and a refused read of them
           still leaves the accounts to show, as on the web. */
        pending: orgPending || (Boolean(orgId) && (catalog.isPending || accounts.isPending || installs.isPending)),
        error: catalog.error ?? accounts.error ?? null,
        refetch: () => { void catalog.refetch(); void accounts.refetch(); void installs.refetch(); },
    };
}

/** Whether this person may make, change or remove installs here: owners
 *  and editors (the web's `useMayInstall`). Anybody may connect an account
 *  of their own on an install that exists. Unknown until the members list
 *  says; nothing is offered or refused before it does. */
export function useMayManage(orgId: string | null): MayManage {
    const { role, answered } = useMyOrgRole(orgId);
    return { may: answered && canManage(role), known: answered && role !== null };
}

/** Read again what a write can change: this organization's accounts and
 *  installs. Not the catalog: no connection adds to it or takes from it. */
export function useRefresh(orgId: string | null) {
    const cache = useQueryClient();
    const key = useCacheKey();
    return () => {
        void cache.invalidateQueries({ queryKey: adminKey(key, PART, "accounts", orgId) });
        void cache.invalidateQueries({ queryKey: adminKey(key, PART, "installs", orgId) });
        void cache.invalidateQueries({ queryKey: adminKey(key, PART, "install", orgId) });
    };
}

/** A mutation that reads the accounts and installs again once it settles,
 *  whichever way: a failed sign-in may still have made an install. */
function useWrite<I, O>(orgId: string | null, write: (orgId: string, input: I) => Promise<O>) {
    const refresh = useRefresh(orgId);
    return useMutation({
        mutationFn: (input: I) => {
            if (!orgId) throw new Error("Pick an organization first.");
            return write(orgId, input);
        },
        onSettled: refresh,
    });
}

/** Sign in through the provider; see `ConnectorsSource.signIn`. */
export function useSignIn(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, input: { connectorId: string; installId: string | null; fields?: Record<string, unknown> }) => source.signIn(org, input));
}

export function useCheckSignIn(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, pending: PendingSignIn) => source.checkSignIn(org, pending));
}

export function useDisconnect(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, accountId: string) => source.disconnect(org, accountId));
}

export function useRotate(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, input: { accountId: string; credentials: Record<string, unknown> }) => source.rotate(org, input.accountId, input.credentials));
}

export function useFinishInstall(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, accountId: string) => source.finishInstall(org, accountId));
}

export function useBindInstallation(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, input: { accountId: string; installationId: string }) => source.bindInstallation(org, input.accountId, input.installationId));
}

export function useMakeDefault(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, install: Install) => source.makeDefault(org, install));
}

export function useRefreshOperations(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, install: Install) => source.refreshOperations(org, install));
}

export function useDeleteInstall(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, install: Install) => source.deleteInstall(org, install));
}

export function useCreateInstall(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, input: NewInstall) => source.createInstall(org, input));
}

/** Submit the connect screen's form (`plan.ts` `submitConnect`). */
export function useSubmitConnect(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, input: { plan: ConnectPlan; connectorId: string; against: Install | null; takenNames: readonly string[]; values: Values }) =>
        submitConnect(source, org, input));
}

/** Add a server, an API or a database of the organization's own (`addOwn`). */
export function useAddOwn(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, input: Parameters<typeof addOwn>[2]) => addOwn(source, org, input));
}

/** Save an install's changes (`saveInstall`). */
export function useSaveInstall(orgId: string | null) {
    const source = useConnectorsSource();
    return useWrite(orgId, (org, input: { install: Install; name: string; list: Field[]; values: Values }) => saveInstall(source, org, input));
}

/** The row's live line on a Lemma's Settings tab: what is true here now.
 *  `text` null while it is read, or when it could not be. The accounts are
 *  the organization's, so every Lemma in it says the same. */
export function useConnectorsLine(podId: string): AboutLine {
    void podId;
    const orgId = useOrgId();
    const catalog = useCatalog();
    const accounts = useAccounts(orgId);
    /* The titles too, unless they will not come: "slack" for a moment is
       a raw id where a name belongs. */
    if (!accounts.isSuccess || catalog.isPending) return { text: null };
    return connectorsLine(catalog.data ?? [], accounts.data);
}
