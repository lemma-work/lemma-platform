import { useMemo, useState, type ReactNode } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useMe, useOrg } from "../../../data/hooks";
import { useSession } from "../../../session/session";
import { Button, Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useDeployment } from "../../admin/access/hooks";
import { useProfile } from "../../admin/account/hooks";
import { Reading, Row, RowIcon, Say, problemOf } from "../../admin/ui";
import { useArrive, useInvitations, useSuggested, useWelcomeSource } from "./hooks";
import { MakeOne } from "./make-one";
import { arrivalLede, inviteLine, landingRoute, sampleArrival, MEMBER_LEDE, memberHeading } from "./model";
import { arrivalHeading, domainOf } from "@web/org/arrival";

/** The first morning, for somebody in no organization (the web's
 *  `ArrivalView`).
 *
 *  Three rungs, closed to open, and the order is the point: an invitation
 *  first, because it is the most specific thing anyone knows about you; an
 *  organization your email domain may join next, because the company being
 *  here already beats making a second one beside it; making one last, and
 *  folded away behind a link when there is something better above. A list
 *  that could not be read is said as such, never drawn as an empty one: that
 *  would offer "make one" to somebody who may well have been invited.
 *
 *  Content only, so Home can draw it in place of its own list and
 *  `(in)/welcome` can draw it as a screen. After any of the three the
 *  organizations are read again, the one entered is chosen, and it lands
 *  on the Lemma an invitation named, else Home (`onEntered` decides how). */
export function Arrival({ onEntered, top }: {
    /** Where to go once in: the Lemma an invitation named, or null. */
    onEntered?: (podId: string | null) => void;
    /** Above the heading: where the install part's "You were installing …"
     *  line goes when there is one. */
    top?: ReactNode;
}) {
    const router = useRouter();
    const { mode } = useSession();
    const me = useMe();
    const profile = useProfile();
    const { org, orgs } = useOrg();
    const source = useWelcomeSource();
    const invitations = useInvitations();
    const suggested = useSuggested();
    const arrive = useArrive();
    /* Both of MakeOne's defaults are read once, from the email's domain and
       the deployment, so it is drawn again when either arrives late. */
    const deployment = useDeployment();
    const [busy, setBusy] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    /* Sample data's organization list never changes, so arriving there is
       said rather than shown. */
    const [kept, setKept] = useState<{ said: string; podId: string | null } | null>(null);

    const land = onEntered ?? ((podId: string | null) => {
        if (podId) router.push(landingRoute(podId) as never);
        else router.navigate(landingRoute(null) as never);
    });

    const email = profile.data?.email ?? me.data?.email ?? null;
    /* The account's own name, never the local part of an address: that is a
       mailbox with a possessive stuck on it. */
    const name = profile.data ? [profile.data.first_name, profile.data.last_name].map((one) => one?.trim()).filter(Boolean).join(" ") || null : null;

    /* An organization you are already in is no longer anywhere to arrive:
       somebody invited and then let in by their email domain still holds
       the invitation, and Account's list is read by members. */
    const invites = useMemo(() => (invitations.data ?? []).filter((invite) => !orgs.some((one) => one.id === invite.orgId)), [invitations.data, orgs]);
    /* An organization that invited you is not also a suggestion: the
       invitation is the more specific fact, and it can carry a Lemma. */
    const matches = useMemo(() => (suggested.data ?? []).filter((one) => !orgs.some((held) => held.id === one.id) && !invites.some((invite) => invite.orgId === one.id)), [suggested.data, invites, orgs]);
    const waiting = invitations.isPending || suggested.isPending;
    const unread = invitations.isError || suggested.isError;
    const checking = invitations.isFetching || suggested.isFetching;
    const domain = domainOf(email);

    const go = async (id: string, run: () => Promise<{ orgId: string; name: string }>, podId: string | null) => {
        setProblem(null);
        setBusy(id);
        try {
            const entered = await run();
            const listed = await arrive(entered.orgId);
            if (mode === "sample" && !listed) setKept({ said: sampleArrival(entered.name, org?.name ?? orgs[0]?.name ?? "Acme"), podId });
            else land(podId);
        } catch (error) {
            setProblem(problemOf(error, "Couldn’t complete organization setup. Try again."));
        } finally {
            setBusy(null);
        }
    };

    if (kept) {
        return (
            <View style={{ gap: 14 }}>
                {top}
                <Say>{kept.said}</Say>
                <View style={{ flexDirection: "row" }}>
                    <Button title="Continue" onPress={() => { const podId = kept.podId; setKept(null); land(podId); }} />
                </View>
            </View>
        );
    }

    return (
        <View style={{ gap: 18 }}>
            {top}
            {waiting ? <Reading label="Looking for invitations" /> : (
                <>
                    <View style={{ gap: 6 }}>
                        <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24, lineHeight: 29 }}>{orgs.length > 0 ? memberHeading(invites.length, matches.length, arrivalHeading(invites.length, matches.length)) : arrivalHeading(invites.length, matches.length)}</Text>
                        <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{orgs.length > 0 && invites.length === 0 && matches.length === 0 ? MEMBER_LEDE : arrivalLede(invites.length, matches.length)}</Text>
                    </View>

                    {unread ? (
                        <View style={{ gap: 6 }}>
                            <Say tone="bad">Couldn’t check for invitations.</Say>
                            <Press
                                label="Try again"
                                disabled={checking}
                                onPress={() => {
                                    if (invitations.isError) void invitations.refetch();
                                    if (suggested.isError) void suggested.refetch();
                                }}
                                style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}
                            >
                                <Text size={14.5} weight="medium">{checking ? "Checking…" : "Try again"}</Text>
                            </Press>
                        </View>
                    ) : null}

                    {invites.length + matches.length > 0 ? (
                        <View>
                            {invites.map((invite, index) => (
                                <Row
                                    key={invite.id}
                                    glyph={<RowIcon name="mail" />}
                                    label={invite.orgName}
                                    hint={inviteLine(invite)}
                                    last={index === invites.length - 1 && matches.length === 0}
                                    end={(
                                        <Button
                                            title={busy === invite.id ? "Coming in…" : "Accept"}
                                            busy={busy === invite.id}
                                            disabled={busy !== null}
                                            onPress={() => void go(invite.id, async () => { await source.acceptInvitation(invite.id); return { orgId: invite.orgId, name: invite.orgName }; }, invite.podId)}
                                        />
                                    )}
                                />
                            ))}
                            {matches.map((one, index) => (
                                <Row
                                    key={one.id}
                                    glyph={<RowIcon name="people" />}
                                    label={one.name}
                                    hint={domain ? `Anyone with an @${domain} address may join` : "Open to your email domain"}
                                    last={index === matches.length - 1}
                                    end={(
                                        <Button
                                            title={busy === one.id ? "Joining…" : "Join"}
                                            busy={busy === one.id}
                                            disabled={busy !== null}
                                            onPress={() => void go(one.id, async () => { await source.joinOrg(one.id); return { orgId: one.id, name: one.name }; }, null)}
                                        />
                                    )}
                                />
                            ))}
                        </View>
                    ) : null}

                    <MakeOne
                        key={`${domain}:${deployment ?? ""}:${orgs.length > 0}`}
                        email={email}
                        name={name}
                        busy={busy === "make"}
                        /* Somebody who already belongs somewhere is making a
                           second one, as the web's New organization does:
                           for a team, and invite-only, since their email
                           domain is usually held by the first. */
                        initialKind={orgs.length > 0 ? "team" : undefined}
                        inviteOnly={orgs.length > 0}
                        secondary={orgs.length > 0 || invites.length > 0 || matches.length > 0 || unread}
                        taken={orgs.map((one) => one.name)}
                        onMake={(wanted) => void go("make", async () => {
                            const made = await source.createOrg(wanted);
                            return { orgId: made.id, name: made.name };
                        }, null)}
                    />

                    {problem ? <Say tone="bad">{problem}</Say> : null}
                </>
            )}
        </View>
    );
}
