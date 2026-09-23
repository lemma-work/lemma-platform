import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { live } from "@/usage/queries";
import type { CatalogEntry, Install } from "./install";

/** Reading and writing installs and accounts.
 *
 *  All of it is in the SDK's `connectors` namespace already — this app simply
 *  never called most of it. `enableApp` is deliberately not used for creating
 *  an install: it reuses an existing one whenever the caller describes nothing
 *  that would distinguish a new one, and every MCP server shares the catalogue
 *  id `mcp`, every database `sql`, every REST API `openapi`. Creating a second
 *  one through it hands back the first and reports success.
 */

/** One catalogue entry, with its kinds and their schemas.
 *
 *  The list endpoint carries kinds too, but not reliably the heavy schema
 *  blobs, so the form asks for the entry it is about to render. Schemas are
 *  catalogue data and change on a deploy, not on a click.
 */
export function useConnector(connectorId: string | null) {
    return useQuery({
        queryKey: ["connector", connectorId ?? ""],
        queryFn: () => lemma().connectors.get(connectorId!) as Promise<CatalogEntry>,
        enabled: live() && Boolean(connectorId),
        staleTime: 10 * 60_000,
        retry: false,
    });
}

/** Every install this organization has, across every connector. */
export function useInstalls(orgId: string | null) {
    return useQuery({
        queryKey: ["installs", orgId ?? ""],
        queryFn: async () => {
            const listed = await lemma().connectors.authConfigs.list(orgId!, { limit: 200 });
            return ((listed as { items?: Install[] }).items ?? []);
        },
        enabled: live() && Boolean(orgId),
        staleTime: 60_000,
        retry: false,
    });
}

/** Everything an install or account write should invalidate.
 *
 *  One place, because these three views are the same facts: the accounts list
 *  drives the connected/available split, the installs list drives what can be
 *  connected against, and the catalogue entry carries the schemas.
 */
export function useConnectorRefresh(orgId: string | null) {
    const cache = useQueryClient();
    return () => {
        void cache.invalidateQueries({ queryKey: ["installs", orgId ?? ""] });
        void cache.invalidateQueries({ queryKey: ["accounts"] });
        void cache.invalidateQueries({ queryKey: ["connectors"] });
    };
}

export interface NewInstall {
    connectorId: string;
    kind?: string;
    name?: string;
    config?: Record<string, unknown>;
    /** `ORG_CUSTOM` when the organization supplies the credentials — its own
     *  OAuth app, or the server it is pointing at. */
    ownCredentials?: boolean;
}

export function useCreateInstall(orgId: string) {
    return useMutation({
        mutationFn: async (input: NewInstall) => {
            const created = await lemma().connectors.authConfigs.create(orgId, {
                connector_id: input.connectorId,
                kind: input.kind,
                name: input.name,
                config: input.config,
                config_source: input.ownCredentials ? "ORG_CUSTOM" : "SYSTEM_DEFAULT",
            });
            return created as unknown as Install;
        },
    });
}

export function useDeleteInstall(orgId: string) {
    return useMutation({
        mutationFn: (install: Install) => lemma().connectors.authConfigs.delete(orgId, install.name || install.id),
    });
}

/** Re-read what an install can do.
 *
 *  Only means anything where the operations are discovered per install —
 *  an MCP server's tool list, an OpenAPI spec — which is why the result
 *  carries a status and not just a count.
 */
export function useRefreshOperations(orgId: string) {
    return useMutation({
        mutationFn: async (install: Install) => {
            const answer = await lemma().connectors.authConfigs.refreshOperations(orgId, install.name || install.id);
            return answer as unknown as { status?: string; operation_count?: number; error?: string | null };
        },
    });
}

/** Connect an account by handing over a credential, rather than by a redirect. */
export function useCreateAccount(orgId: string) {
    return useMutation({
        mutationFn: async (input: { installId: string; credentials: Record<string, unknown> }) => {
            const made = await lemma().connectors.accounts.create(orgId, {
                auth_config_id: input.installId,
                credentials: input.credentials,
            });
            return made as unknown as { id?: string };
        },
    });
}

/** Replace an account's credential, keeping the account.
 *
 *  Deleting and reconnecting also rotates one, and issues a new account id
 *  doing it — stranding every schedule, surface and grant pinned to the old
 *  one, and leaving nothing at all behind if the reconnect then fails.
 */
export function useRotateCredentials(orgId: string) {
    return useMutation({
        mutationFn: (input: { accountId: string; credentials: Record<string, unknown> }) =>
            lemma().connectors.rotateAccountCredentials(orgId, input.accountId, input.credentials),
    });
}
