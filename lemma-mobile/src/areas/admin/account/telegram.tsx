import { useState } from "react";
import { ScrollView, View } from "react-native";
import { Loading } from "../../../ui/chrome";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { LinkTelegramSteps } from "../../chats/identity-link";
import { Group, Row, Say, problemOf } from "../ui";
import { useMyTelegramSurfaces, useRereadTelegramAccounts, useTelegramAccounts, useTelegramBots, useUnlinkTelegram } from "./hooks";
import { LINK_HINT, botToOpen, linkButton, linkedRow, telegramShown } from "./link-telegram";
import { botDetail, botTitle, type BotChoice, type MyTelegramSurfaces } from "@web/session/telegram-bots";

/** Telegram on the profile (the web's `LinkTelegramEntry`): the accounts
 *  already linked, each to unlink, and the button that links one. The
 *  button only for somebody one of whose Lemmas has a Telegram bot --
 *  without one there is nothing to open the code with -- and the group only
 *  when there is something to show. */
export function TelegramGroup() {
    const mine = useMyTelegramSurfaces();
    const accounts = useTelegramAccounts();
    const unlink = useUnlinkTelegram();
    const reread = useRereadTelegramAccounts();
    const [open, setOpen] = useState(false);
    const linked = accounts.data ?? [];
    const through = mine.data?.surfaceIds.length ? mine.data : null;
    const unread = accounts.isError && !accounts.data;
    if (!telegramShown({ pending: accounts.isPending, unread, bots: through ? through.surfaceIds.length : 0, linked: linked.length })) return null;
    return (
        <Group label="Telegram">
            {unread ? (
                <View style={{ gap: 4, paddingTop: 4 }}>
                    <Say tone="bad">{problemOf(accounts.error, "Couldn’t read your linked accounts.")}</Say>
                    <Press label="Try again" onPress={() => void accounts.refetch()} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
                        <Text size={14.5} weight="medium">Try again</Text>
                    </Press>
                </View>
            ) : linked.length ? linked.map((account, index) => {
                const row = linkedRow(account);
                return (
                    <Row
                        key={account.id}
                        label={row.label}
                        value={row.value}
                        glyph={<Icon name="telegram" size={17} tone={color.ink2} />}
                        end={(
                            <Press label={`Unlink ${row.label}`} disabled={unlink.isPending} onPress={() => unlink.mutate(account.id)} style={{ minHeight: 40, justifyContent: "center", paddingLeft: 8, opacity: unlink.isPending ? 0.45 : 1 }}>
                                <Text size={14.5} tone={color.amber}>Unlink</Text>
                            </Press>
                        )}
                        last={index === linked.length - 1}
                    />
                );
            }) : (
                <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20, paddingTop: 2 }}>{LINK_HINT}</Text>
            )}
            {unlink.isError ? <Say tone="bad">{problemOf(unlink.error, "Couldn’t unlink it.")}</Say> : null}
            {through ? (
                <View style={{ flexDirection: "row", paddingTop: 8 }}>
                    <Button title={linkButton(linked.length)} kind="outline" onPress={() => setOpen(true)} />
                </View>
            ) : null}
            <Sheet open={open && Boolean(through)} onClose={() => { setOpen(false); reread(); }} title="Link your Telegram" tall>
                {open && through ? <LinkFromProfile mine={through} onClose={() => { setOpen(false); reread(); }} /> : null}
            </Sheet>
        </Group>
    );
}

/** A profile sits in no one Lemma, so the sheet first asks which bot
 *  Telegram should open. The proof holds on every bot, so the choice is
 *  between bots, not Lemmas: Lemma's shared bot once, first and ticked,
 *  then each Lemma's own. With one bot there is nothing to choose. */
function LinkFromProfile({ mine, onClose }: { mine: MyTelegramSurfaces; onClose: () => void }) {
    const bots = useTelegramBots(mine);
    const reread = useRereadTelegramAccounts();
    const [picked, setPicked] = useState<string | null>(null);
    const [going, setGoing] = useState<BotChoice | null>(null);
    const { through, selected } = botToOpen(bots.data ?? [], picked, going);

    if (through) {
        return <LinkTelegramSteps key={through.surfaceId} podId={through.podId} podName={through.podName} surfaceId={through.surfaceId} everywhere onLinked={reread} onClose={onClose} />;
    }
    if (bots.isPending) return <Loading label="Finding your Lemmas’ Telegram bots…" />;
    if (bots.isError) {
        return (
            <View style={{ gap: 12, paddingHorizontal: 20, paddingBottom: 8 }}>
                <Say tone="bad">{problemOf(bots.error, "Couldn’t find your Lemmas’ Telegram bots.")}</Say>
                <Button title="Try again" kind="outline" height={46} onPress={() => void bots.refetch()} />
            </View>
        );
    }
    if (!selected) {
        return (
            <View style={{ gap: 8, paddingHorizontal: 20, paddingBottom: 8 }}>
                <Text size={16} style={{ lineHeight: 22 }}>None of your Lemmas has a Telegram bot yet.</Text>
                <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>Once one does, link your account through it here.</Text>
                <View style={{ paddingTop: 4 }}><Button title="Done" kind="outline" height={46} onPress={onClose} /></View>
            </View>
        );
    }
    const choices = bots.data ?? [];
    return (
        <>
            <View style={{ gap: 4, paddingHorizontal: 20, paddingBottom: 6 }}>
                <Text size={16} style={{ lineHeight: 22 }}>Which bot should Telegram open?</Text>
                <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>Whichever you pick, every Lemma you’re in knows the account as you.</Text>
            </View>
            <ScrollView accessibilityRole="radiogroup" accessibilityLabel="Telegram bot" contentContainerStyle={{ paddingHorizontal: 20 }}>
                {choices.map((choice, index) => {
                    const on = choice.surfaceId === selected.surfaceId;
                    return (
                        <Press
                            key={choice.surfaceId}
                            role="radio"
                            label={`${botTitle(choice)}, ${botDetail(choice)}`}
                            accessibilityState={{ selected: on }}
                            onPress={() => setPicked(choice.surfaceId)}
                            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, paddingVertical: 6, borderBottomWidth: index === choices.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                        >
                            <Text size={16} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                                {botTitle(choice)}
                                <Text size={13.5} tone={color.ink3}>{"  " + botDetail(choice)}</Text>
                            </Text>
                            {on ? <Icon name="check" size={18} width={2} /> : null}
                        </Press>
                    );
                })}
            </ScrollView>
            <View style={{ paddingHorizontal: 20, paddingTop: 10 }}>
                <Button title="Continue" height={48} onPress={() => setGoing(selected)} />
            </View>
        </>
    );
}
