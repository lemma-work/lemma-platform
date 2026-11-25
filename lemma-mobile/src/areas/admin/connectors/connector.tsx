import { useMemo } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Button } from "../../../ui/press";
import { Counted } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Group, ReadProblem, Reading, Row, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { isBringYourOwn, tenantKind } from "@web/connect/install";
import { useConnectorHere, useMayManage } from "./hooks";
import { accountHint, accountTitle, activeInstalls, holdings, installWhere, installsLabel, installsShown, stateWords } from "./model";
import { Actions, Logo } from "./parts";
import { connectTarget, installActs } from "./plan";
import { connectorsRoutes } from "./routes";

/** One connector (the web's connector card): what it is, how to connect
 *  it, the installs worth naming, and your accounts on it, each opening its
 *  own screen. Connecting needs an install to run against; where there is
 *  none, making one is an owner's or editor's, and that is said up front
 *  rather than as the refusal a tap would earn. */
export function ConnectorScreen({ connectorId }: { connectorId: string }) {
    const router = useRouter();
    const here = useConnectorHere(connectorId);
    const manage = useMayManage(here.orgId);
    const { connector, accounts, installs } = here;
    const held = useMemo(() => holdings(accounts, installs), [accounts, installs]);

    if (here.noOrg) return <SettingsScreen title="Connectors" back="Connectors"><Say>Connectors belong to an organization, and you are not in one yet.</Say></SettingsScreen>;
    if (here.pending) return <SettingsScreen title="Connectors" back="Connectors"><Reading /></SettingsScreen>;
    if (here.error) {
        return (
            <SettingsScreen title="Connectors" back="Connectors">
                <ReadProblem said={isForbidden(here.error) ? "You may not see this organization’s connectors." : problemOf(here.error, "This connector could not be read. Try again.")} onRetry={here.refetch} />
            </SettingsScreen>
        );
    }
    if (!connector) return <SettingsScreen title="Connectors" back="Connectors"><Say>There is no such connector here.</Say></SettingsScreen>;

    const addressed = isBringYourOwn(connector);
    const on = activeInstalls(installs);
    const named = installsShown(connector, installs);
    const target = connectTarget(installs, null);
    /* Nothing to connect against, and not this person's to make. */
    const refused = manage.known && !manage.may && (addressed || on.length === 0);
    const ownKind = tenantKind(connector)?.kind ?? null;

    return (
        <SettingsScreen title={connector.title} note={connector.description || null} back="Connectors">
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                <Logo connector={connector} size={36} />
                <Counted size={15} tone={held.accounts.get(connector.id)?.some((one) => !one.usable) ? color.ink : color.ink2} style={{ flex: 1 }}>{stateWords(connector, held)}</Counted>
            </View>

            {refused ? (
                <Say>An owner or editor of this organization has to set {connector.title} up. Then anybody here can connect an account.</Say>
            ) : addressed ? (
                manage.may && ownKind ? (
                    <Actions>
                        <Button title={on.length ? "Add another" : "Add one"} onPress={() => router.push(connectorsRoutes.add(connector.id, ownKind) as never)} />
                    </Actions>
                ) : null
            ) : on.length > 0 || manage.may ? (
                <Actions>
                    <Button
                        title={accounts.length ? "Connect another account" : "Connect"}
                        onPress={() => router.push(connectorsRoutes.connect(connector.id, { install: target?.id ?? null }) as never)}
                    />
                </Actions>
            ) : null}

            {named && installs.length > 0 ? (
                <Group label={installsLabel(connector)}>
                    {installs.map((install, index) => {
                        const acts = installActs(install, connector, accounts);
                        const hint = [
                            installWhere(install, connector),
                            install.is_default && installs.length > 1 ? "Default" : null,
                            acts.off ? "Off" : null,
                            acts.needsSignIn && !acts.off ? "Nobody has signed in yet" : null,
                        ].filter(Boolean).join(" · ");
                        return (
                            <Row
                                key={install.id}
                                role="link"
                                label={install.name || install.id}
                                hint={hint}
                                needs={acts.needsSignIn && !acts.off}
                                last={index === installs.length - 1}
                                onPress={() => router.push(connectorsRoutes.install(connector.id, install.id) as never)}
                            />
                        );
                    })}
                </Group>
            ) : null}

            {accounts.length > 0 ? (
                <Group label="Your accounts" foot="Removing an account stops everything that uses it.">
                    {accounts.map((account, index) => (
                        <Row
                            key={account.id}
                            role="link"
                            label={accountTitle(account, connector)}
                            hint={accountHint(account, installs.find((one) => one.id === account.authConfigId) ?? null, named) || null}
                            needs={!account.usable}
                            last={index === accounts.length - 1}
                            onPress={() => router.push(connectorsRoutes.account(connector.id, account.id) as never)}
                        />
                    ))}
                </Group>
            ) : !addressed && !refused ? (
                <Say>None of your accounts are connected to {connector.title} yet.</Say>
            ) : null}
        </SettingsScreen>
    );
}
