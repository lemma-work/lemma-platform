import { useMemo, useState } from "react";
import { View } from "react-native";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useDeployment } from "../../admin/access/hooks";
import { Field, Say } from "../../admin/ui";
import { KINDS } from "./model";
import type { NewOrg } from "./source";
import { canOpenToDomain, defaultOrgKind, domainOf, freshTeamNameFor, personalNameFor } from "@web/org/arrival";

/** Just you, or your team (the web's `MakeOne`).
 *
 *  Asked, never inferred: the email domain decides only which is chosen
 *  first and whether the team may be left open to that domain. A shared
 *  mailbox provider never can, since "my team" on a Gmail address means
 *  "I will invite them", not "let every Gmail address in". On a local
 *  installation nobody else can reach the server yet, so "Just me" is
 *  chosen first there. Key it on the email's domain and the deployment,
 *  so both defaults are read again when either arrives late. */
export function MakeOne({ email, name, secondary, busy, initialKind, inviteOnly = false, taken = [], onMake }: {
    email: string | null;
    name: string | null;
    /** Folded behind a link when something better is on the screen above. */
    secondary: boolean;
    busy: boolean;
    /** Which kind starts chosen; read from the email when absent. */
    initialKind?: "personal" | "team";
    /** Never open the team to its email domain: a domain belongs to one
     *  organization, and for a second one it is usually held by the first. */
    inviteOnly?: boolean;
    /** Names already in use by this person's organizations. */
    taken?: readonly string[];
    onMake: (wanted: NewOrg) => void;
}) {
    const local = useDeployment() === "local";
    const domain = domainOf(email);
    const canOpen = !inviteOnly && canOpenToDomain(email);
    const suggestion = useMemo(() => freshTeamNameFor(email, taken), [email, taken]);
    const [kind, setKind] = useState<"personal" | "team">(() => initialKind ?? defaultOrgKind(email, local));
    const [called, setCalled] = useState(suggestion);
    const [open, setOpen] = useState(!secondary);

    if (!open) {
        return (
            <Press role="button" label="Or start an organization of your own" onPress={() => setOpen(true)} style={{ alignSelf: "flex-start", minHeight: 44, justifyContent: "center" }}>
                <Text size={15} weight="medium">Or start an organization of your own</Text>
            </Press>
        );
    }

    const personalName = personalNameFor(name);
    const ready = kind === "personal" || called.trim().length > 0;
    const make = () => {
        if (busy || !ready) return;
        onMake(kind === "team"
            ? { name: called.trim(), ...(canOpen ? { emailDomain: domain } : {}) }
            /* A name nobody typed: the server may make it unique rather than
               refuse a second Ada a name she never chose. */
            : { name: personalName, derived: true });
    };

    return (
        <View style={{ gap: 14 }}>
            {secondary ? <Text size={17} weight="medium">Start one of your own</Text> : null}
            <View accessibilityRole="radiogroup">
                {(["personal", "team"] as const).map((one, index) => {
                    const on = kind === one;
                    return (
                        <Press
                            key={one}
                            role="radio"
                            label={`${KINDS[one].title}. ${KINDS[one].line}`}
                            accessibilityState={{ selected: on }}
                            onPress={() => setKind(one)}
                            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 58, paddingVertical: 8, borderBottomWidth: index === 1 ? 0 : 1, borderBottomColor: color.line }}
                        >
                            <View style={{ flex: 1, gap: 2 }}>
                                <Text size={16} weight={on ? "medium" : "regular"}>{KINDS[one].title}</Text>
                                <Text size={13.5} tone={color.ink3} style={{ lineHeight: 18 }}>{KINDS[one].line}</Text>
                            </View>
                            <View style={{ width: 22, height: 22, borderRadius: 11, borderWidth: on ? 0 : 1.5, borderColor: color.line2, backgroundColor: on ? color.ink : "transparent", alignItems: "center", justifyContent: "center" }}>
                                {on ? <Icon name="check" size={13} width={2.4} tone={color.onInk} /> : null}
                            </View>
                        </Press>
                    );
                })}
            </View>

            {local ? <Say>Others can join once you turn on Sharing on the computer Lemma runs on.</Say> : null}

            {kind === "team" ? (
                <View style={{ gap: 8 }}>
                    <Field label="What is your team called?" value={called} onChange={setCalled} placeholder="Acme" autoCapitalize="words" onSubmit={make} />
                    <Say>
                        {canOpen
                            ? `Anyone with an @${domain} address can join without being invited.`
                            : inviteOnly
                                ? "You add people by inviting them. You can open it to your email domain later, in People."
                                : "You add people by inviting them."}
                    </Say>
                </View>
            ) : (
                <Say>{personalName === "Personal" ? "Private to you. You can invite people later." : `Private to you. It will be called ${personalName}.`}</Say>
            )}

            <Button
                title={busy ? "Setting it up…" : kind === "team" ? `Create ${called.trim() || "organization"}` : "Create my organization"}
                busy={busy}
                disabled={!ready}
                height={48}
                onPress={make}
            />
        </View>
    );
}
