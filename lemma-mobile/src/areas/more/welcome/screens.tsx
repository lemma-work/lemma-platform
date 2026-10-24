import { useState } from "react";
import { ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useMe, useOrg } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import { useSession } from "../../../session/session";
import { BackLink, Loading } from "../../../ui/chrome";
import { Button } from "../../../ui/press";
import { color } from "../../../ui/tokens";
import { useProfile } from "../../admin/account/hooks";
import { useDeployment } from "../../admin/access/hooks";
import { Group, ReadProblem, Row, Say, SettingsScreen, isForbidden, problemOf } from "../../admin/ui";
import { Arrival } from "./arrival";
import { useCreateOrg, useDecide, useInvitation } from "./hooks";
import { MakeOne } from "./make-one";
import { ResumeInstall } from "../install/resume";
import {
    UNREADABLE_INVITATION, invitationStanding, landingRoute, podFromRedirect, podRoleWord, roleWord, sampleArrival, spokenDate,
} from "./model";

/** The first morning as a screen of its own (`(in)/welcome`): what Home
 *  shows somebody with no organization, reachable by somebody who has
 *  some, and in sample data. */
export function WelcomeScreen() {
    const router = useRouter();
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", paddingTop: 4, paddingLeft: 6 }}>
                <BackLink />
            </View>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
                <Arrival
                    top={<ResumeInstall />}
                    onEntered={(podId) => {
                        if (podId) router.replace(landingRoute(podId) as never);
                        else router.dismissTo(routes.home as never);
                    }}
                />
            </ScrollView>
        </SafeAreaView>
    );
}

/** A second organization, made from inside the app (the web's
 *  `NewOrgDialog`): the same choice as the first morning, with My team
 *  chosen, since somebody who has a place and asks for another is usually
 *  setting one up for other people. Invite-only: a domain belongs to one
 *  organization, and theirs most likely holds it already. */
export function NewOrgScreen() {
    const router = useRouter();
    const { mode } = useSession();
    const me = useMe();
    const profile = useProfile();
    const { org, orgs } = useOrg();
    const local = useDeployment() === "local";
    const create = useCreateOrg();
    const [kept, setKept] = useState<string | null>(null);
    const email = profile.data?.email ?? me.data?.email ?? null;
    const name = profile.data ? [profile.data.first_name, profile.data.last_name].map((one) => one?.trim()).filter(Boolean).join(" ") || null : null;

    return (
        <SettingsScreen title="New organization" note={local ? "Its own Lemmas, people and connectors." : "Its own Lemmas, people, connectors and billing."} back="Account">
            {kept ? (
                <View style={{ gap: 12 }}>
                    <Say>{kept}</Say>
                    <View style={{ flexDirection: "row" }}>
                        <Button title="Back to Home" onPress={() => router.dismissTo(routes.home as never)} />
                    </View>
                </View>
            ) : profile.isPending && !me.data ? (
                <Loading label="Reading your account" />
            ) : (
                <>
                    <MakeOne
                        email={email}
                        name={name}
                        secondary={false}
                        initialKind="team"
                        inviteOnly
                        taken={orgs.map((one) => one.name)}
                        busy={create.isPending}
                        onMake={(wanted) => create.mutate(wanted, {
                            onSuccess: ({ org: made, listed }) => {
                                if (mode === "sample" && !listed) setKept(sampleArrival(made.name, org?.name ?? "Acme"));
                                else router.dismissTo(routes.home as never);
                            },
                        })}
                    />
                    {create.isError ? <Say tone="bad">{problemOf(create.error, "Couldn’t create the organization. Try again.")}</Say> : null}
                </>
            )}
        </SettingsScreen>
    );
}

/** An invitation opened from its link (`lemma://invitations/{id}/accept`
 *  or `/reject`, the web's paths; the web's `InvitationDecision`): what it
 *  is, and Accept or Decline as the path says. */
export function InvitationScreen({ id, decision }: { id: string; decision: "accept" | "reject" }) {
    const router = useRouter();
    const { mode } = useSession();
    const { org } = useOrg();
    const invitation = useInvitation(id);
    const decide = useDecide(id);
    const [done, setDone] = useState<{ decision: "accept" | "reject"; land: string; kept: string | null } | null>(null);

    if (invitation.isPending) {
        return <SettingsScreen title="Invitation" back="Home"><Loading label="Reading the invitation" /></SettingsScreen>;
    }
    if (invitation.isError) {
        return (
            <SettingsScreen title="Invitation" back="Home">
                <ReadProblem said={UNREADABLE_INVITATION} onRetry={() => void invitation.refetch()} />
            </SettingsScreen>
        );
    }

    const invite = invitation.data;
    const standing = invitationStanding(invite);
    const lapses = spokenDate(invite.expiresAt);
    const podRole = podRoleWord(invite.podRole);
    /* When it lapses matters only while it could still be acted on. */
    const timed = !done && (invite.status === "PENDING" || invite.status === "EXPIRED");
    const lapsed = invite.status === "EXPIRED" || !standing.open;
    const title = invite.podName ? `Work with ${invite.podName}` : `Join ${invite.orgName}`;

    const act = () => decide.mutate({ decision, orgId: invite.orgId }, {
        onSuccess: (result) => {
            const podId = podFromRedirect(result.redirectUri ?? invite.redirectUri) ?? invite.podId;
            setDone({
                decision: result.decision,
                land: result.decision === "accept" ? landingRoute(podId) : routes.home,
                kept: result.decision === "accept" && mode === "sample" && !result.listed ? sampleArrival(invite.orgName, org?.name ?? "Acme") : null,
            });
        },
    });

    /* The server lets only an organization's own people revoke one of its
       invitations, so the person invited cannot decline it yet. Said as
       what to do, not as the server's refusal. */
    const refused = decide.isError && decision === "reject" && isForbidden(decide.error);

    return (
        <SettingsScreen title={title} note={invite.podAbout} back="Home">
            <Group>
                <Row label="Organization" value={invite.orgName} />
                {invite.podName ? <Row label="To work with" value={invite.podName} /> : null}
                <Row label="Sent to" value={invite.email || "Your account"} />
                <Row label="Your role" value={roleWord(invite.role)} last={!timed && !(invite.podName && podRole)} />
                {invite.podName && podRole ? <Row label={`In ${invite.podName}`} value={podRole} last={!timed} /> : null}
                {timed ? <Row label={lapsed ? "Lapsed" : "Lapses"} value={lapses || "Not said"} last /> : null}
            </Group>

            {done ? (
                <View style={{ gap: 12 }}>
                    <Say tone="done">{done.decision === "accept" ? `You’re in ${invite.orgName}.` : "You declined this invitation."}</Say>
                    {done.kept ? <Say>{done.kept}</Say> : null}
                    <View style={{ flexDirection: "row" }}>
                        <Button title="Continue" height={48} onPress={() => router.replace(done.land as never)} />
                    </View>
                </View>
            ) : standing.open ? (
                <View style={{ gap: 12 }}>
                    {refused ? (
                        <Say>{`It can’t be declined from here yet: only ${invite.orgName} can withdraw it. Leaving it does no harm${lapses ? `, and it lapses on ${lapses}` : ""}.`}</Say>
                    ) : decide.isError ? (
                        <Say tone="bad">{problemOf(decide.error, "Couldn’t update the invitation. Try again.")}</Say>
                    ) : null}
                    <Button
                        title={decide.isPending ? (decision === "accept" ? "Coming in…" : "Declining…") : decision === "accept" ? "Accept invitation" : "Decline invitation"}
                        height={48}
                        busy={decide.isPending}
                        disabled={refused}
                        onPress={act}
                    />
                </View>
            ) : (
                <View style={{ gap: 12 }}>
                    <Say>{standing.said}</Say>
                    <View style={{ flexDirection: "row" }}>
                        <Button title="Go to Home" kind="outline" onPress={() => router.dismissTo(routes.home as never)} />
                    </View>
                </View>
            )}
        </SettingsScreen>
    );
}
