import type { LemmaClient } from "lemma-sdk";
import { readAccount, readConnector, type Connector, type ConnectorAccount } from "@web/data/accounts";
import { canInstallWithDefaults, freshInstallName, heldInstall, primaryKind, type CatalogEntry, type ConfigSource, type Install } from "@web/connect/install";

/** The organization's connector accounts, read and started the way the web
 *  does (`lemma-frontend/src/data/live.ts`), for the two places that connect
 *  one: a Lemma's channels and the organization's connectors. Live only; each
 *  area's sample answers the same shapes itself. */

type Listish = { items?: unknown[] } | unknown[];

function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    return (value as { items?: unknown[] } | null)?.items ?? [];
}

/** What starting an authorisation answers: the provider's page, which
 *  accounts were usable before it, and the install it runs against. */
export interface AccountConnect {
    authorizeUrl: string;
    before: string[];
    authConfigId: string;
}

export async function listConnectors(client: LemmaClient): Promise<Connector[]> {
    const listed = (await client.connectors.list({ limit: 200 })) as Listish;
    return itemsOf(listed)
        .map(readConnector)
        .filter((entry): entry is Connector => entry !== null)
        .sort((a, b) => a.title.localeCompare(b.title));
}

export async function listAccounts(client: LemmaClient, orgId: string, connectorId?: string): Promise<ConnectorAccount[]> {
    const listed = (await client.connectors.accounts.list(orgId, { limit: 200, ...(connectorId ? { connectorId } : {}) })) as Listish;
    return itemsOf(listed).map(readAccount).filter((entry): entry is ConnectorAccount => entry !== null);
}

export async function disconnectAccount(client: LemmaClient, orgId: string, accountId: string): Promise<void> {
    await client.connectors.accounts.delete(orgId, accountId);
}

/** Start an account's authorisation: settle the install first (the one
 *  named, else the organization's default of the kind asked for, else
 *  Lemma's own, made now), then ask for the provider's page. An install made
 *  here and left with nobody on it is removed again when the request fails,
 *  so a retry is not refused for the name it holds. */
export async function startAccount(
    client: LemmaClient,
    orgId: string,
    connectorId: string,
    options: { authConfigId?: string; returnTo?: string; connectionFields?: Record<string, unknown>; configSource?: ConfigSource } = {},
): Promise<AccountConnect> {
    const before = (await listAccounts(client, orgId, connectorId)).filter((account) => account.usable).map((account) => account.id);

    let installId = options.authConfigId;
    let madeHere: { id: string; name: string } | null = null;
    if (!installId) {
        const installs = ((await client.connectors.authConfigs.list(orgId, { limit: 200 })).items ?? []) as Install[];
        const held = heldInstall(installs, connectorId, options.configSource);
        if (held) {
            installId = held.id;
        } else {
            const entry = (await client.connectors.get(connectorId)) as CatalogEntry;
            const kind = primaryKind(entry);
            if (!canInstallWithDefaults(kind)) {
                throw new Error(`${entry.title} has no sign-in app here yet. One has to be added under Connectors before anybody can connect it.`);
            }
            const made = await client.connectors.authConfigs.create(orgId, {
                connector_id: connectorId,
                kind: kind?.kind,
                config_source: "SYSTEM_DEFAULT",
                name: freshInstallName(connectorId, installs.map((one) => one.name)),
            } as never);
            madeHere = { id: String((made as { id?: unknown }).id), name: String((made as { name?: unknown }).name) };
            installId = madeHere.id;
        }
    }

    try {
        const request = (await client.connectors.createConnectRequest(orgId, {
            auth_config_id: installId,
            ...(options.returnTo ? { return_to: options.returnTo } : {}),
            ...(options.connectionFields ? { connection_fields: options.connectionFields } : {}),
        } as never)) as { authorization_url?: string | null };
        return { authorizeUrl: request.authorization_url ?? "", before, authConfigId: installId };
    } catch (problem) {
        if (madeHere) await client.connectors.authConfigs.delete(orgId, madeHere.name).catch(() => undefined);
        throw problem;
    }
}

/** The account an authorisation made: usable now, and not usable when it
 *  started (a new row, or one brought back to health). "" when there is none
 *  yet: the person closed the page before finishing, or it is still on its
 *  way. Never an account that was already there. */
export async function findAccount(client: LemmaClient, orgId: string, connectorId: string, before: readonly string[], authConfigId?: string): Promise<string> {
    const accounts = await listAccounts(client, orgId, connectorId);
    const seen = new Set(before);
    return accounts.find((account) => (!authConfigId || account.authConfigId === authConfigId) && !seen.has(account.id) && account.usable)?.id ?? "";
}

/** Make an account, then bind it; undo the account if the bind is refused
 *  (the web's `createThenBind`): an account left behind by a refused bind
 *  has nobody on it and would be offered back as ready. */
export async function createThenBind<T>(
    create: () => Promise<string>,
    bind: (accountId: string) => Promise<T>,
    undo: (accountId: string) => Promise<unknown>,
): Promise<T> {
    const accountId = await create();
    try {
        return await bind(accountId);
    } catch (problem) {
        await undo(accountId).catch(() => undefined);
        throw problem;
    }
}
