import type { Connector } from "@/data/accounts";
import type { PodConnector } from "@/data/pod-connectors";
import { canInstallWithDefaults, isBringYourOwn, primaryKind, type Install } from "./install";

/** What a pod's connector can be added from, and what a link's
 *  `?connector=` names. Pure: the rules are about lists, not about React. */

/** One entry in the "Add a connector" picker: an installation the
 *  organization already has, or an app from the catalog nobody installed yet
 *  — added by its catalog id, and the server installs Lemma's own app for it
 *  when the person may. */
export type AddChoice =
    | { key: string; from: "install"; installId: string; connectorId: string; label: string }
    | { key: string; from: "catalog"; installId: ""; connectorId: string; label: string };

/** The picker's choices: the organization's installations first, then the
 *  catalog's apps it has none of that sign in with Lemma's own app.
 *
 *  Only those from the catalog: one that needs the organization's own OAuth
 *  app, a server address or a credential form cannot be added in one step,
 *  and offering it here ends in a refusal. Those are set up from the
 *  organization's Connectors page. A catalog app the organization already
 *  has an installation of is left out too — that installation is the choice.
 *  `installs` may be empty because the person may not list them; the
 *  catalog then offers everything, and the server picks the organization's
 *  default installation for a connector it already has. */
export function addChoices(installs: readonly Install[], catalog: readonly Connector[], titleOf: (connectorId: string) => string): AddChoice[] {
    const installed = new Set(installs.map((one) => one.connector_id));
    const fromInstalls: AddChoice[] = installs.map((one) => ({
        key: "install:" + one.id,
        from: "install",
        installId: one.id,
        connectorId: one.connector_id,
        label: titleOf(one.connector_id) + (one.name && one.name !== one.connector_id ? " — " + one.name : ""),
    }));
    const fromCatalog: AddChoice[] = catalog
        .filter((entry) => entry.active !== false && !installed.has(entry.id))
        .filter((entry) => !isBringYourOwn(entry) && canInstallWithDefaults(primaryKind(entry)))
        .map((entry) => ({ key: "catalog:" + entry.id, from: "catalog" as const, installId: "" as const, connectorId: entry.id, label: entry.title || entry.id }))
        .sort((a, b) => a.label.localeCompare(b.label));
    return [...fromInstalls, ...fromCatalog];
}

/** What `?connector=<name>` on a pod's Connections page points at.
 *
 *  Agents hand people that link for whatever is not connected yet, and the
 *  name in it is one of three things: a pod connector's slug, an
 *  organization installation's name, or a catalog id the pod has no
 *  connector for at all (`gmail` in a brand-new organization). A pod
 *  connector wins — its row is where Connect is — and is also found by the
 *  installation or app it runs through, since the agent may have named
 *  either. Otherwise an installation or a catalog app is something to
 *  connect from here; only a name matching nothing is "gone". */
export type FocusTarget =
    | { kind: "connector"; slug: string }
    | { kind: "connect"; connectorId: string; installId: string; title: string }
    | { kind: "unknown" }
    | { kind: "none" };

export function focusTarget(
    focus: string | null,
    connectors: readonly PodConnector[],
    installs: readonly Install[],
    catalog: readonly Pick<Connector, "id" | "title">[],
): FocusTarget {
    const name = (focus ?? "").trim();
    if (!name) return { kind: "none" };
    const lower = name.toLowerCase();
    const bySlug = connectors.find((one) => one.slug === name);
    if (bySlug) return { kind: "connector", slug: bySlug.slug };
    const install = installs.find((one) => one.name.toLowerCase() === lower)
        ?? installs.find((one) => one.connector_id === lower && one.is_default)
        ?? installs.find((one) => one.connector_id === lower);
    const throughIt = connectors.find((one) =>
        (install && one.authConfigId === install.id)
        || one.installationName.toLowerCase() === lower
        || one.connectorId === lower);
    if (throughIt) return { kind: "connector", slug: throughIt.slug };
    const titleOf = (id: string) => catalog.find((one) => one.id === id)?.title || id;
    if (install) return { kind: "connect", connectorId: install.connector_id, installId: install.id, title: titleOf(install.connector_id) };
    const entry = catalog.find((one) => one.id === lower);
    if (entry) return { kind: "connect", connectorId: entry.id, installId: "", title: entry.title || entry.id };
    return { kind: "unknown" };
}
