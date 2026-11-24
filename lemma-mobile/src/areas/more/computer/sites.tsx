import { useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { Glyph } from "../../../thread/glyphs";
import { Button, Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { Confirm, Group, Row } from "../../admin/ui";
import { problemOf } from "../../admin/words";
import { Quiet, Trouble } from "./files";
import { useLogins, useSignOutOf } from "./hooks";
import { siteGroups, siteNote, signOutAsk } from "./model";
import { saidSoFor } from "@web/computer/logins";

/** The sites the computer's browser is signed in to (the web's
 *  `logins-view.tsx`). Read from the browser itself every time, so it asks
 *  only while this place is open, and never wakes the computer unless asked
 *  to. */
export function SitesPlace({ enabled, onWoke }: { enabled: boolean; onWoke?: () => void }) {
    const [wake, setWake] = useState(false);
    const [asking, setAsking] = useState<string | null>(null);
    const logins = useLogins(wake, enabled);
    const signOut = useSignOutOf();
    const groups = siteGroups(logins.data?.items ?? []);
    const now = new Date();

    if (logins.isPending) {
        return (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 14 }}>
                <ActivityIndicator color={color.ink3} />
                <Text size={14} tone={color.ink3}>Asking the browser…</Text>
            </View>
        );
    }
    if (logins.isError) return <Trouble said={problemOf(logins.error, "The browser couldn’t be asked just now.")} onRetry={() => void logins.refetch()} />;
    if (logins.data.sleeping) {
        return (
            <View style={{ gap: 12, paddingVertical: 8 }}>
                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>This computer is asleep, and its browser is the only thing that knows which sites it is signed in to.</Text>
                <View style={{ flexDirection: "row" }}><Button title="Wake it and ask" onPress={() => { setWake(true); onWoke?.(); }} /></View>
            </View>
        );
    }
    if (groups.length === 0) {
        return <Quiet>Nothing yet. You’re asked to sign in when a site needs a person, and the browser keeps that session afterwards.</Quiet>;
    }

    const ask = asking ? signOutAsk(asking) : null;
    return (
        <View style={{ gap: 18 }}>
            {groups.map((group) => (
                <Group key={group.label ?? "sites"} label={group.label ?? undefined}>
                    {group.sites.map((login, index) => (
                        <Row
                            key={login.site}
                            label={login.site}
                            hint={siteNote(login, now)}
                            glyph={<Glyph name={saidSoFor(login) ? "lock" : "globe"} size={17} tone={color.ink2} />}
                            last={index === group.sites.length - 1}
                            end={(
                                <Press label={`Sign out of ${login.site}`} onPress={() => { signOut.reset(); setAsking(login.site); }} style={{ minHeight: TOUCH, justifyContent: "center", paddingLeft: 8 }}>
                                    <Text size={14} tone={color.ink2}>Sign out</Text>
                                </Press>
                            )}
                        />
                    ))}
                </Group>
            ))}
            <Confirm
                open={asking !== null}
                title={ask?.title ?? ""}
                body={ask?.body ?? ""}
                confirm="Sign out"
                busy={signOut.isPending}
                error={signOut.isError ? problemOf(signOut.error, "That didn’t sign out. It needs the computer running, since it really signs the browser out.") : null}
                onClose={() => setAsking(null)}
                onConfirm={() => {
                    if (asking) signOut.mutate(asking, { onSuccess: () => setAsking(null) });
                }}
            />
        </View>
    );
}
