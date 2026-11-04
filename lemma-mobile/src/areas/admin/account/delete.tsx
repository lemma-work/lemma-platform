import { useState } from "react";
import { Alert, View } from "react-native";
import { useRouter } from "expo-router";
import { useMe } from "../../../data/hooks";
import { useSession } from "../../../session/session";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Field, Group, Say, SettingsScreen } from "../ui";
import { useDeleteAccount } from "./hooks";
import { confirmsAccount, DELETION_CONSEQUENCES, deletionRefusal, type DeletionRefusal } from "@web/session/account-deletion";

/** Deleting your own account (the web's `session/delete-account.tsx`): what
 *  it does, in the web's own words, then the address typed back as the
 *  confirmation. On success the server has already ended every session,
 *  this phone's included; signing out here only forgets the phone's copy,
 *  and the next screen is sign-in. */
export function DeleteAccountScreen() {
    const router = useRouter();
    const me = useMe();
    const { signOut } = useSession();
    const remove = useDeleteAccount();
    const email = me.data?.email ?? "";
    const [typed, setTyped] = useState("");
    const [refusal, setRefusal] = useState<DeletionRefusal | null>(null);
    const confirmed = confirmsAccount(typed, email);

    const leave = () => {
        Alert.alert("Your account is deleted", "You are signed out everywhere. You can sign up again with the same email address, as a new account.");
        void signOut();
    };

    const submit = () => {
        setRefusal(null);
        remove.mutate(typed.trim(), {
            onSuccess: leave,
            onError: (problem) => {
                const refused = deletionRefusal(problem);
                /* A second tap, after the first went through. */
                if (refused.alreadyDeleted) leave();
                else setRefusal(refused);
            },
        });
    };

    return (
        <SettingsScreen title="Delete your account" note="This cannot be undone." back="Account">
            <Group label="What happens">
                <View style={{ gap: 10, paddingTop: 4 }}>
                    {DELETION_CONSEQUENCES.map((line) => (
                        <View key={line} style={{ flexDirection: "row", gap: 10 }}>
                            <Text size={15} tone={color.ink3}>–</Text>
                            <Text size={15} style={{ flex: 1, lineHeight: 21 }}>{line}</Text>
                        </View>
                    ))}
                </View>
            </Group>

            <Group label="Confirm">
                <View style={{ gap: 12, paddingTop: 4 }}>
                    <Field
                        label={email ? `Type ${email} to confirm` : "Type your email address to confirm"}
                        value={typed}
                        onChange={(next) => { setTyped(next); setRefusal(null); }}
                        placeholder={email}
                        keyboardType="email-address"
                        autoCapitalize="none"
                        editable={!remove.isPending}
                    />
                    {refusal ? (
                        <View style={{ gap: 4 }}>
                            <Say tone="bad">{refusal.message}</Say>
                            {refusal.organizations.length > 0 ? <Say>Change who owns each under People, in its settings.</Say> : null}
                        </View>
                    ) : null}
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        <Button title="Keep my account" kind="outline" grow disabled={remove.isPending} onPress={() => router.back()} />
                        <Button title="Delete my account" grow busy={remove.isPending} disabled={!confirmed} onPress={submit} />
                    </View>
                </View>
            </Group>
        </SettingsScreen>
    );
}
