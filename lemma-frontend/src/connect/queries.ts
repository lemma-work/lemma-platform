import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { live } from "@/usage/queries";
import type { CatalogEntry, Install } from "./install";
import { canManage } from "@/org/membership";

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

/** Whether this person may create or change installs here.
 *
 *  Connecting an account needs only membership; making an install needs an
 *  owner or an editor. The backend answers a member who tries with a 404 about
 *  "no connectors in organization <uuid>" — true to its rule of not saying who
 *  is in what, and useless on a button. So the page asks first.
 *
 *  Found the way the People panel finds it, from the same cached reads: the
 *  members list is the only thing that says what I am here. `null` when that
 *  cannot be told — nothing is withheld on a guess, and the backend still
 *  refuses what it must. */
export function useMayInstall(orgId: string | null): boolean | null {
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: live(),
        staleTime: 5 * 60_000,
    });
    const members = useQuery({
        queryKey: ["org-members", orgId ?? ""],
        queryFn: () => lemma().organizations.members.list(orgId!, { limit: 100 }),
        enabled: live() && Boolean(orgId),
    });
    const listed = members.data as { items?: { user_id?: string; role?: string }[] } | undefined;
    const mine = listed?.items?.find((member) => member.user_id === me.data?.id);
    if (!mine) return null;
    return canManage(mine.role);
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

/** Make an install the one a bare connector id resolves to.
 *
 *  An organization holding two of one connector — two Slack apps, a Gmail on
 *  Lemma's client and one on its own — is otherwise stuck with whichever it
 *  created first, for every caller that names the connector and not the
 *  install. */
export function useMakeDefaultInstall(orgId: string) {
    return useMutation({
        mutationFn: (install: Install) =>
            lemma().connectors.authConfigs.update(orgId, install.name || install.id, { is_default: true }),
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

/** One GitHub installation an account could speak for. */
export interface InstallationChoice {
    installation_id: string;
    account_login?: string | null;
    account_type?: string | null;
    repository_selection?: string | null;
}

/** What finishing an install came to: nothing left to do, a choice to make,
 *  or GitHub's install page to go to. */
export type InstallStep = { done: true } | { choices: InstallationChoice[] } | { url: string };

/** Finish an authorised-but-not-installed account — a GitHub App's.
 *
 *  Signing in to a GitHub App yields a token that reaches only repositories
 *  the App is *installed* on, so the account is connected and can read nothing
 *  until somebody installs it. Reconnecting cannot fix that, which is all this
 *  page used to offer. GitHub is asked first: somebody who installed it from
 *  GitHub's own page is already done, and sending them to install again is
 *  wrong. The install link is minted last, because its state is single-use and
 *  expires. */
export function useFinishInstall(orgId: string, returnTo: () => string) {
    return useMutation({
        mutationFn: async (accountId: string): Promise<InstallStep> => {
            const client = lemma().connectors;
            const found = (await client.accountInstallations(orgId, accountId, true)) as {
                install_state?: string | null; choices?: InstallationChoice[] | null;
            };
            if (found.install_state === "READY") return { done: true };
            if (found.install_state === "CHOOSE_INSTALL") return { choices: found.choices ?? [] };
            const request = await client.createInstallRequest(orgId, accountId, returnTo());
            return { url: String((request as { authorization_url?: string | null }).authorization_url ?? "") };
        },
    });
}

/** Settle which installation an account speaks for, when it reaches several. */
export function useBindInstallation(orgId: string) {
    return useMutation({
        mutationFn: (input: { accountId: string; installationId: string }) =>
            lemma().connectors.bindAccountInstallation(orgId, input.accountId, input.installationId),
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
