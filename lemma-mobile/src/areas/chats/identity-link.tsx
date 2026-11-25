import { Linking, View } from "react-native";
import { Button } from "../../ui/press";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { Trouble } from "./bits";
import { useIdentityLink } from "./hooks";
import { accountSaid, linkEnded, type TelegramAccount } from "@web/channels/identity-link-model";
import { problemOf } from "@web/channels/problem";

/** A person proves which Telegram account is theirs, so a Lemma's bot knows
 *  them in its groups (the web's `channels/identity-link.tsx`): a one-time
 *  code opened in Telegram from that account, then confirmed here, in their
 *  own session. Nothing is linked until they say the account they see is
 *  theirs: a link is easy to pass on, and whoever opened a forwarded one
 *  would otherwise get their access.
 *
 *  Drawn inside whichever sheet chose the bot: a group's own ("This is me"
 *  in its details) or the one picked on the profile. `everywhere` says so
 *  where nothing on screen names a Lemma: the account is the person's in
 *  every Lemma they are in, not only the one the code was made in. On a
 *  phone, "Open Telegram" hands the bot's t.me link to Telegram itself. */
export function LinkTelegramSteps({ podId, podName, surfaceId, contactId, seenAs, everywhere = false, onLinked, onClose }: {
    podId: string;
    podName: string;
    /** The bot to open it with. */
    surfaceId: string;
    /** Somebody in a group the Lemma took for a stranger, who the person
     *  says is them: the code then works only from that account. */
    contactId?: string;
    /** How that somebody shows in the group, to say what is being linked. */
    seenAs?: string | null;
    everywhere?: boolean;
    onLinked?: (account: TelegramAccount) => void;
    onClose: () => void;
}) {
    const proof = useIdentityLink({ podId, surfaceId, contactId, onLinked, onDeclined: onClose });
    const where = everywhere ? "every Lemma you’re in" : podName;
    const url = proof.link?.url ?? null;
    return (
        <View style={{ gap: 12, paddingHorizontal: 20, paddingTop: 4, paddingBottom: 8 }}>
            {proof.step === "linked" && proof.linked ? (
                <Lead>{`Linked. ${everywhere ? "Every Lemma you’re in knows" : podName + " knows"} ${accountSaid(proof.linked)} as you from the next message.`}</Lead>
            ) : proof.step === "confirm" && proof.account ? (
                <>
                    <Lead>{`Is ${accountSaid(proof.account)} your Telegram account?`}</Lead>
                    <Fine>{`Only say yes if it is. Linked, it gets your access in ${where}.`}</Fine>
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        <Button title="Not me" kind="outline" grow height={46} disabled={proof.declining || proof.confirming} onPress={proof.decline} />
                        <Button title={proof.confirming ? "Linking…" : "Yes, link it"} grow height={46} busy={proof.confirming} disabled={proof.declining} onPress={proof.confirm} />
                    </View>
                </>
            ) : proof.step === "other-account" ? (
                <Lead>{`That link was opened from a different Telegram account than ${seenAs ?? "the one in the group"}, so nothing was linked.`}</Lead>
            ) : proof.step === "gone" ? (
                <Lead>That link has expired. Close this and try again.</Lead>
            ) : (
                <>
                    <Lead>{`${seenAs ? `To show ${podName} that ${seenAs} is you, open` : "Open"} this in Telegram from your account and tap Start.`}</Lead>
                    <Button title={url ? "Open Telegram" : "Getting a link…"} height={48} busy={!url && !proof.problem} disabled={!url} onPress={() => url && void Linking.openURL(url).catch(() => undefined)} />
                    <Fine>Then come back here to say it is yours. The link works for ten minutes.</Fine>
                </>
            )}
            <Trouble>{proof.problem ? problemOf(proof.problem, "Couldn’t link it.") : null}</Trouble>
            {linkEnded(proof.step) ? <Button title="Done" kind="outline" height={46} onPress={onClose} /> : null}
        </View>
    );
}

function Lead({ children }: { children: string }) {
    return <Text size={16} style={{ lineHeight: 22 }}>{children}</Text>;
}

function Fine({ children }: { children: string }) {
    return <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>{children}</Text>;
}
