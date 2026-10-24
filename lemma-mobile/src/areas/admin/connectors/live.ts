import type { LiveContext } from "../../context";
import { listAccounts, listConnectors, disconnectAccount, startAccount } from "../connect/accounts";
import { authorize } from "../connect/round-trip";
import type { CatalogEntry, Install } from "@web/connect/install";
import { redirectUriOf } from "./redirect";
import { PHONE_RETURN, readSignIn, snapshot } from "./sign-in";
import type { ConnectorsSource, Discovery, InstallStep, InstallationChoice, PendingSignIn } from "./source";

/** The same calls the web makes (`data/live.ts` `listConnectors`,
 *  `listAccounts`, `startAccount`, `findAccount`, `disconnectAccount`, and
 *  `connect/queries.ts` for installs, credentials and installations), all
 *  against the organization, never a Lemma. An install is named in the API
 *  by its name, or its id when it has none. */
export function connectorsLive(context: LiveContext): ConnectorsSource {
    const client = () => context.client();
    const named = (install: Install) => install.name || install.id;

    const discovery = (raw: unknown): Discovery => {
        const said = (raw ?? {}) as { status?: unknown; operation_count?: unknown; reason?: unknown };
        return {
            status: typeof said.status === "string" ? said.status : null,
            count: typeof said.operation_count === "number" ? said.operation_count : null,
            reason: typeof said.reason === "string" ? said.reason : null,
        };
    };

    const choicesOf = (raw: unknown): InstallationChoice[] =>
        (Array.isArray(raw) ? raw : []).map((one: { installation_id?: unknown; account_login?: unknown; account_type?: unknown; repository_selection?: unknown }) => ({
            installationId: String(one.installation_id ?? ""),
            login: typeof one.account_login === "string" ? one.account_login : null,
            type: typeof one.account_type === "string" ? one.account_type : null,
            repositories: typeof one.repository_selection === "string" ? one.repository_selection : null,
        })).filter((one) => one.installationId);

    /** What a GitHub App account reaches, asked of GitHub again: a change
     *  made there sends no callback. */
    const installations = async (orgId: string, accountId: string) =>
        (await client().connectors.accountInstallations(orgId, accountId, true)) as { install_state?: string | null; choices?: unknown };

    const look = async (orgId: string, pending: PendingSignIn, outcome: Parameters<typeof readSignIn>[2] = null) =>
        readSignIn(pending, await listAccounts(client(), orgId, pending.connectorId), outcome);

    return {
        catalog: () => listConnectors(client()),

        async detail(connectorId) {
            const detail = await client().connectors.get(connectorId);
            return { entry: detail as unknown as CatalogEntry, redirectUri: redirectUriOf(detail) };
        },

        accounts: (orgId) => listAccounts(client(), orgId),

        async installs(orgId) {
            const listed = await client().connectors.authConfigs.list(orgId, { limit: 200 });
            return ((listed as { items?: Install[] }).items ?? []);
        },

        async install(orgId, install) {
            return (await client().connectors.authConfigs.get(orgId, named(install))) as unknown as Install;
        },

        async signIn(orgId, { connectorId, installId, fields }) {
            /* Every account as it stood, not only the usable ones: a sign-in
               that leaves a new account unable to reach anything (a GitHub App
               not yet installed) has to be told from one that was already
               there in that state. */
            const before = snapshot(await listAccounts(client(), orgId, connectorId));
            const started = await startAccount(client(), orgId, connectorId, {
                ...(installId ? { authConfigId: installId } : {}),
                returnTo: PHONE_RETURN,
                ...(fields && Object.keys(fields).length ? { connectionFields: fields } : {}),
            });
            if (!started.authorizeUrl) return { kind: "no-page" };
            const outcome = await authorize(started.authorizeUrl);
            return look(orgId, { connectorId, authConfigId: started.authConfigId, before }, outcome);
        },

        checkSignIn: (orgId, pending) => look(orgId, pending),

        async createAccount(orgId, installId, credentials) {
            const made = (await client().connectors.accounts.create(orgId, { auth_config_id: installId, credentials })) as { id?: unknown };
            return String(made.id ?? "");
        },

        disconnect: (orgId, accountId) => disconnectAccount(client(), orgId, accountId),

        async rotate(orgId, accountId, credentials) {
            await client().connectors.rotateAccountCredentials(orgId, accountId, credentials);
        },

        async finishInstall(orgId, accountId): Promise<InstallStep> {
            /* GitHub first: somebody who installed it from GitHub's own page is
               already done. The install link is minted last, because its state
               is single-use and expires. */
            const found = await installations(orgId, accountId);
            if (found.install_state === "READY") return { kind: "installed" };
            if (found.install_state === "CHOOSE_INSTALL") return { kind: "choices", choices: choicesOf(found.choices) };
            const request = (await client().connectors.createInstallRequest(orgId, accountId, PHONE_RETURN)) as { authorization_url?: string | null };
            if (!request.authorization_url) return { kind: "no-page" };
            await authorize(request.authorization_url);
            const after = await installations(orgId, accountId);
            if (after.install_state === "READY") return { kind: "installed" };
            if (after.install_state === "CHOOSE_INSTALL") return { kind: "choices", choices: choicesOf(after.choices) };
            return { kind: "not-yet" };
        },

        async bindInstallation(orgId, accountId, installationId) {
            await client().connectors.bindAccountInstallation(orgId, accountId, installationId);
        },

        async createInstall(orgId, input) {
            const made = await client().connectors.authConfigs.create(orgId, {
                connector_id: input.connectorId,
                kind: input.kind,
                name: input.name,
                config: input.config,
                config_source: input.own ? "ORG_CUSTOM" : "SYSTEM_DEFAULT",
            });
            return made as unknown as Install;
        },

        async updateInstall(orgId, install, change) {
            const answer = (await client().connectors.authConfigs.update(orgId, named(install), {
                ...(change.name !== undefined ? { name: change.name } : {}),
                ...(change.config !== undefined ? { config: change.config } : {}),
            })) as unknown as { auth_config?: Install; operations_discovery?: unknown; accounts_marked_for_reauth?: number | null };
            return {
                install: answer.auth_config ?? install,
                discovery: answer.operations_discovery ? discovery(answer.operations_discovery) : null,
                reauth: answer.accounts_marked_for_reauth ?? 0,
            };
        },

        async makeDefault(orgId, install) {
            await client().connectors.authConfigs.update(orgId, named(install), { is_default: true });
        },

        async refreshOperations(orgId, install) {
            return discovery(await client().connectors.authConfigs.refreshOperations(orgId, named(install)));
        },

        async deleteInstall(orgId, install) {
            await client().connectors.authConfigs.delete(orgId, named(install));
        },
    };
}
