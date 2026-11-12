import { part } from "../../paths";

/** The organization's connectors, under `(in)/settings/connectors/…`:
 *
 *  - `catalog`: `(in)/settings/[section]` with "connectors", every connector;
 *  - `connector`: one connector, its accounts and installs;
 *  - `connect`: connecting an account on it (or setting up the
 *    organization's own app first, `own`), on an install when one is named;
 *  - `add`: adding a server, an API or a database of the organization's own,
 *    as an install of the catalog entry that stands for every one of them;
 *  - `account`, `rotate`: one of your accounts, and replacing its credential;
 *  - `install`, `edit`: one install, and changing its settings. */
const BASE = "/settings/connectors";

const at = (connectorId: string, ...rest: string[]) => [BASE, part(connectorId), ...rest.map(part)].join("/");

export const connectorsRoutes = {
    catalog: () => BASE,
    connector: (connectorId: string) => at(connectorId),
    connect: (connectorId: string, options: { install?: string | null; own?: boolean } = {}) => {
        const query = [options.install ? `install=${part(options.install)}` : "", options.own ? "own=1" : ""].filter(Boolean).join("&");
        return at(connectorId, "connect") + (query ? `?${query}` : "");
    },
    add: (connectorId: string, kind: string) => `${at(connectorId, "add")}?kind=${part(kind)}`,
    account: (connectorId: string, accountId: string) => at(connectorId, "account", accountId),
    rotate: (connectorId: string, accountId: string) => at(connectorId, "account", accountId, "rotate"),
    install: (connectorId: string, installId: string) => at(connectorId, "install", installId),
    edit: (connectorId: string, installId: string) => at(connectorId, "install", installId, "edit"),
};
