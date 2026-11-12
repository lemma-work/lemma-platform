import { useMemo, useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useOrg } from "../../../data/hooks";
import { Press } from "../../../ui/press";
import { Counted, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Group, ReadProblem, Reading, Row, RowIcon, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { useAccounts, useCatalog, useInstalls, useMayManage } from "./hooks";
import {
    bringYourOwn, bringYourOwnWords, emptyWords, holdings, metaWords, poolOf, rowHint, searchConnectors, shownSlice, sliceCatalog,
    type Slice,
} from "./model";
import { AddOwnSheet, Logo, SearchBox, Slices } from "./parts";
import { connectorsRoutes } from "./routes";

export { AccountScreen, RotateScreen } from "./account";
export { AddOwnScreen, ConnectScreen } from "./connect";
export { ConnectorScreen } from "./connector";
export { EditInstallScreen, InstallScreen } from "./install";

/** How many rows a slice draws before "Show all": the catalog runs to
 *  hundreds, and a search finds the rest. */
const FIRST = 60;

/** The organization's connectors (the web's `ConnectorsSection`): what
 *  you have connected and whether it works first, the rest of the catalog
 *  one slice over, and a search across whichever slice is shown. A row
 *  opens the connector, where its accounts and installs are. Adding one of
 *  the organization's own (a server, an API, a database, an OAuth app) is
 *  for owners and editors, and only where the catalog offers it.
 *
 *  Reached from the account screen, and from every Lemma's Settings: the
 *  installs are the organization's, so every Lemma in it sees the same
 *  screen. The accounts listed are the signed-in person's own there, which
 *  is all the server lists, changes or removes for anybody. */
export function ConnectorsScreen({ back = "Account" }: { back?: string } = {}) {
    const router = useRouter();
    const { org, pending: orgPending } = useOrg();
    const orgId = org?.id ?? null;
    const catalog = useCatalog();
    const accounts = useAccounts(orgId);
    const installs = useInstalls(orgId);
    const manage = useMayManage(orgId);
    const [query, setQuery] = useState("");
    const [slice, setSlice] = useState<Slice | null>(null);
    const [adding, setAdding] = useState(false);
    const [everything, setEverything] = useState(false);

    const all = useMemo(() => catalog.data ?? [], [catalog.data]);
    const held = useMemo(() => holdings(accounts.data ?? [], installs.data ?? []), [accounts.data, installs.data]);
    const sliced = useMemo(() => sliceCatalog(all, accounts.data ?? [], held), [all, accounts.data, held]);
    const own = useMemo(() => bringYourOwn(all), [all]);
    const active = shownSlice(slice, sliced);
    const shown = searchConnectors(poolOf(active, sliced), query);
    const drawn = everything || query.trim() ? shown : shown.slice(0, FIRST);
    const offerOwn = manage.may && (own.kinds.length > 0 || own.ownApp);

    const failed = catalog.error ?? accounts.error;
    const reading = orgPending || (Boolean(orgId) && (catalog.isPending || accounts.isPending));

    return (
        <SettingsScreen title="Connectors" back={back}>
            {/* The heading's note, set here so its counts are in Inter. */}
            {accounts.isSuccess ? <Counted size={14.5} tone={color.ink2} style={{ marginTop: -8, lineHeight: 20 }}>{metaWords(accounts.data)}</Counted> : null}
            {!orgId && !orgPending ? (
                <Say>Connectors belong to an organization, and you are not in one yet.</Say>
            ) : reading ? (
                <Reading />
            ) : failed ? (
                <ReadProblem
                    said={isForbidden(failed) ? "You may not see this organization’s connectors." : problemOf(failed, "Connectors could not be read. Check the connection and try again.")}
                    onRetry={() => { void catalog.refetch(); void accounts.refetch(); void installs.refetch(); }}
                />
            ) : (
                <>
                    {offerOwn ? (
                        <Group>
                            <Row
                                role="button"
                                glyph={<RowIcon name="plus" />}
                                label="Add your own"
                                hint={bringYourOwnWords(own)}
                                last
                                onPress={() => setAdding(true)}
                            />
                        </Group>
                    ) : null}

                    <View style={{ gap: 10 }}>
                        <SearchBox value={query} onChange={setQuery} placeholder={`Search ${all.length} connectors`} label="Search connectors" />
                        <Slices
                            active={active}
                            counts={{ connected: sliced.connected.length, available: sliced.available.length, trouble: sliced.ailing.length }}
                            onPick={(next) => { setSlice(next); setEverything(false); }}
                        />
                    </View>

                    {shown.length === 0 ? (
                        <View style={{ gap: 8 }}>
                            <Say>{emptyWords(query, active, all.length)}</Say>
                            {!query.trim() && all.length === 0 ? (
                                <Press label="Read the catalog again" onPress={() => void catalog.refetch()} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
                                    <Text size={14.5} weight="medium">Read it again</Text>
                                </Press>
                            ) : null}
                        </View>
                    ) : (
                        <Group
                            foot={drawn.length < shown.length ? (
                                <Press label={`Show all ${shown.length}`} onPress={() => setEverything(true)} style={{ minHeight: 44, justifyContent: "center", alignSelf: "flex-start" }}>
                                    <Text size={14.5} weight="medium">Show all {shown.length}</Text>
                                </Press>
                            ) : active !== "available" && (accounts.data?.length ?? 0) > 0 ? "Removing an account stops everything that uses it." : null}
                        >
                            {drawn.map((connector, index) => {
                                const said = rowHint(connector, held, manage);
                                return (
                                    <Row
                                        key={connector.id}
                                        role="link"
                                        glyph={<Logo connector={connector} />}
                                        label={connector.title}
                                        hint={said.hint}
                                        needs={said.needs}
                                        last={index === drawn.length - 1}
                                        onPress={() => router.push(connectorsRoutes.connector(connector.id) as never)}
                                    />
                                );
                            })}
                        </Group>
                    )}
                    {installs.isError && !isForbidden(installs.error) ? (
                        <Text size={13} tone={color.ink3}>The organization’s installs could not be read, so servers it added may be missing here.</Text>
                    ) : null}
                </>
            )}
            <AddOwnSheet open={adding} onClose={() => setAdding(false)} kinds={own.kinds} connectors={all} ownApp={own.ownApp} />
        </SettingsScreen>
    );
}
