import { useState, type ReactNode } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useOrg } from "../../../data/hooks";
import type { Org } from "../../../data/types";
import { PersonFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Counted, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useDeployment } from "../access/hooks";
import { settingsEntry } from "../sections";
import { ChoiceRow, ChoiceSheet, Copyable, Field, Group, ReadProblem, Reading, Row, RowIcon, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import {
    canSetJoinPolicy, linkOnlyOpensHere, memberName, roleLabel, unsentInvitation, type Member, type Role,
} from "@web/org/membership";
import { useInvite, useOrgJoin, useOrgPeople, useRemoveMember, useRevoke, useSetOrgJoin, useSetRole } from "./hooks";
import {
    cleanDomain, expiryWords, invitationHint, inviteWrong, memberHint, memberReach, pickJoin, roleBlurb, rolesFor,
    rolesToGive, rosterWords,
} from "./model";
import { orgRoutes } from "./routes";
import type { Invitation } from "./source";
import { domainProblem, orgJoinChoices, orgJoinSaid, type OrgJoin, type OrgJoinPolicy } from "@web/data/joining";

/** The organization, or a plain word that there is none, for each of this
 *  part's screens. */
function WithOrg({ title, note, back, children }: { title: string; note?: string | null; back: string; children: (org: Org) => ReactNode }) {
    const { org, pending } = useOrg();
    if (org) return <>{children(org)}</>;
    return (
        <SettingsScreen title={title} note={note} back={back}>
            {pending ? <Reading /> : <Say>You are not in an organization yet, so there is nobody here to show.</Say>}
        </SettingsScreen>
    );
}

/** Who is in the organization, and how somebody else gets to be (the web's
 *  `org/people.tsx`): asking somebody in is the first row, then the people
 *  here, the invitations not answered yet, and who may let themselves in.
 *  What is offered follows what the signed-in person may do; the server
 *  refuses the rest. */
export function OrgPeopleScreen() {
    const entry = settingsEntry("people");
    const title = entry?.title ?? "People";
    return (
        <WithOrg title={title} note={entry?.hint} back="Account">
            {(org) => <OrgPeople org={org} title={title} note={entry?.hint ?? null} />}
        </WithOrg>
    );
}

function OrgPeople({ org, title, note }: { org: Org; title: string; note: string | null }) {
    const router = useRouter();
    const people = useOrgPeople(org.id);
    const local = useDeployment() === "local";
    const [member, setMember] = useState<Member | null>(null);
    const [invite, setInvite] = useState<Invitation | null>(null);
    const { members, myId, myRole, manage, pending } = people;

    return (
        <SettingsScreen title={title} note={note} back="Account">
            {manage ? (
                <Group>
                    <Row label="Invite someone" hint="By email, with the role they will have" glyph={<RowIcon name="plus" />} onPress={() => router.push(orgRoutes.invite as never)} last />
                </Group>
            ) : null}

            {/* Who is here: the count over them, in Inter. */}
            <View style={{ gap: 2 }}>
                {members.isSuccess ? (
                    <Counted size={12.5} tone={color.ink3} style={{ lineHeight: 22 }}>{rosterWords(people.people.length, pending.length)}</Counted>
                ) : null}
                {members.isPending ? <Reading /> : members.isError ? (
                    isForbidden(members.error)
                        ? <Say>You are not a member of {org.name}, so its people cannot be read from here.</Say>
                        : <ReadProblem said={problemOf(members.error, "The people here could not be read.")} onRetry={() => void members.refetch()} />
                ) : people.people.map((one, index) => {
                    const mine = Boolean(myId) && one.user_id === myId;
                    const reach = memberReach(myRole, people.people, one, myId);
                    return (
                        <Row
                            key={one.id}
                            glyph={<PersonFace name={memberName(one)} size={28} />}
                            label={memberName(one)}
                            hint={memberHint(one, mine)}
                            value={roleLabel(one.role)}
                            onPress={reach.opens ? () => setMember(one) : undefined}
                            last={index === people.people.length - 1}
                        />
                    );
                })}
            </View>

            {manage && pending.length > 0 ? (
                <Group label="Invited, not joined yet">
                    {pending.map((one, index) => (
                        <Row
                            key={one.id}
                            glyph={<RowIcon name="mail" />}
                            label={one.email ?? "Somebody"}
                            hint={invitationHint(one, manage)}
                            needs={one.emailed === false}
                            onPress={() => setInvite(one)}
                            last={index === pending.length - 1}
                        />
                    ))}
                </Group>
            ) : manage && people.invitations.isError ? (
                <Group label="Invited, not joined yet">
                    <ReadProblem said={problemOf(people.invitations.error, "The invitations could not be read.")} onRetry={() => void people.invitations.refetch()} />
                </Group>
            ) : null}

            <WhoCanJoin org={org} mayChange={people.answered ? canSetJoinPolicy(myRole) : members.isError ? false : null} />

            {member ? (
                <MemberSheet
                    org={org}
                    member={member}
                    myRole={myRole}
                    members={people.people}
                    myId={myId}
                    onClose={() => setMember(null)}
                />
            ) : null}
            {invite ? <InvitationSheet org={org} invite={invite} local={local} onClose={() => setInvite(null)} /> : null}
        </SettingsScreen>
    );
}

/** One of a few, drawn in a sheet that is already open: a tick on the one
 *  held, a spinner on the one being saved. */
function RadioRows<T extends string>({ choices, value, saving, onPick, disabled = false }: {
    choices: readonly { value: T; label: string; hint?: string | null }[];
    value: T | null;
    saving: T | null;
    onPick: (value: T) => void;
    disabled?: boolean;
}) {
    return (
        <View accessibilityRole="radiogroup">
            {choices.map((choice, index) => {
                const on = choice.value === value;
                return (
                    <Press
                        key={choice.value}
                        role="radio"
                        label={choice.label}
                        disabled={disabled || on}
                        accessibilityState={{ selected: on, disabled }}
                        onPress={() => onPick(choice.value)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: choice.hint ? 58 : 50, paddingVertical: 6, borderBottomWidth: index === choices.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                    >
                        <View style={{ flex: 1, gap: 1 }}>
                            <Text size={16}>{choice.label}</Text>
                            {choice.hint ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{choice.hint}</Text> : null}
                        </View>
                        {saving === choice.value ? <ActivityIndicator size="small" color={color.ink3} /> : on ? <Icon name="check" size={18} width={2} /> : null}
                    </Press>
                );
            })}
        </View>
    );
}

/** One member: their role to change, and the way to take them out, for
 *  whoever may. Taking somebody out asks first, in the same sheet. */
function MemberSheet({ org, member, myRole, members, myId, onClose }: {
    org: Org;
    member: Member;
    myRole: string | null;
    members: Member[];
    myId: string | null;
    onClose: () => void;
}) {
    const setRole = useSetRole(org.id);
    const remove = useRemoveMember(org.id);
    const [asking, setAsking] = useState(false);
    /* The row as it is now, so a role just saved shows its tick. */
    const now = members.find((one) => one.id === member.id) ?? member;
    const reach = memberReach(myRole, members, now, myId);
    const name = memberName(now);
    const email = now.user?.email ?? now.email ?? "";

    return (
        <Sheet open onClose={() => { if (!remove.isPending) onClose(); }} title={email && email !== name ? `${name} · ${email}` : name} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 14 }}>
                {asking ? (
                    <View style={{ gap: 14 }}>
                        <Text size={18} weight="medium">Remove {name}?</Text>
                        <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>
                            {name} will no longer be a member of {org.name}. You can invite them again later.
                        </Text>
                        {remove.isError ? (
                            <Say tone="bad">{isForbidden(remove.error) ? `You may not remove ${name}.` : problemOf(remove.error, `${name} was not removed. Try again.`)}</Say>
                        ) : null}
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Keep" kind="outline" grow disabled={remove.isPending} onPress={() => { remove.reset(); setAsking(false); }} />
                            <Button title={remove.isPending ? "Removing…" : "Remove"} grow busy={remove.isPending} onPress={() => remove.mutate(now.id, { onSuccess: onClose })} />
                        </View>
                    </View>
                ) : (
                    <>
                        <Group label="Role">
                            {reach.changeRole ? (
                                <RadioRows
                                    choices={rolesFor(myRole, now.role)}
                                    value={(now.role ?? "ORG_MEMBER") as Role}
                                    saving={setRole.isPending ? setRole.variables?.role ?? null : null}
                                    disabled={setRole.isPending}
                                    onPick={(role) => setRole.mutate({ memberId: now.id, role })}
                                />
                            ) : (
                                <Row label={roleLabel(now.role)} hint={roleBlurb(now.role)} last />
                            )}
                        </Group>
                        {reach.why ? <Say>{reach.why}</Say> : null}
                        {setRole.isError ? (
                            <Say tone="bad">{isForbidden(setRole.error) ? `You may not change ${name}'s role.` : problemOf(setRole.error, "That role was not changed. Try again.")}</Say>
                        ) : null}
                        {reach.remove ? (
                            <View style={{ flexDirection: "row", paddingTop: 4 }}>
                                <Button title={`Remove from ${org.name}`} kind="outline" onPress={() => setAsking(true)} />
                            </View>
                        ) : null}
                    </>
                )}
            </ScrollView>
        </Sheet>
    );
}

/** People on other devices cannot open a link to this computer's own
 *  address until Sharing is on, on an installation on somebody's computer. */
function OnlyOpensHere({ link, local }: { link: string | null | undefined; local: boolean }) {
    if (!local || !linkOnlyOpensHere(link)) return null;
    return <Say tone="bad">This link only opens on the computer Lemma runs on. Turn on Sharing in the Lemma app there before you send it.</Say>;
}

/** An invitation nobody has answered: what it gives, when it runs out, its
 *  link when nobody was emailed, and the way to withdraw it. */
function InvitationSheet({ org, invite, local, onClose }: { org: Org; invite: Invitation; local: boolean; onClose: () => void }) {
    const revoke = useRevoke(org.id);
    const unsent = unsentInvitation(invite);
    const expiry = expiryWords(invite.expires_at);
    return (
        <Sheet open onClose={onClose} title={invite.email ?? "An invitation"} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 14 }} keyboardShouldPersistTaps="handled">
                <Group>
                    <Row label="Joins as" value={roleLabel(invite.role)} hint={roleBlurb(invite.role)} last={!expiry} />
                    {expiry ? <Row label={expiry} last /> : null}
                </Group>
                {unsent ? (
                    <View style={{ gap: 8 }}>
                        <Say>{unsent.said}</Say>
                        {unsent.link ? <Copyable value={unsent.link} label="invitation link" mono /> : null}
                        <OnlyOpensHere link={unsent.link} local={local} />
                    </View>
                ) : invite.emailed ? <Say>They were emailed a link to join {org.name}.</Say> : null}
                {revoke.isError ? (
                    <Say tone="bad">{isForbidden(revoke.error) ? "You may not withdraw this invitation." : problemOf(revoke.error, "That invitation was not withdrawn. Try again.")}</Say>
                ) : null}
                <View style={{ flexDirection: "row", paddingTop: 4 }}>
                    <Button
                        title={revoke.isPending ? "Withdrawing…" : "Withdraw invitation"}
                        kind="outline"
                        busy={revoke.isPending}
                        onPress={() => revoke.mutate(invite.id, { onSuccess: onClose })}
                    />
                </View>
            </ScrollView>
        </Sheet>
    );
}

/** Who may let themselves in (the web's `org/who-can-join-org.tsx`): owners
 *  change it, everybody else reads it, since a rule nobody can see is one
 *  nobody can ask an owner to change. `mayChange` is null while the people
 *  that say what you are here are still read. */
function WhoCanJoin({ org, mayChange }: { org: Org; mayChange: boolean | null }) {
    const router = useRouter();
    const current = useOrgJoin(org.id);
    const set = useSetOrgJoin(org.id);
    const [picking, setPicking] = useState(false);
    const foot = "No Lemma here can be opened wider than the organization around it.";

    if (current.isError) {
        return (
            <Group label="Who else can join" foot={foot}>
                <ReadProblem said={problemOf(current.error, "Who can join could not be read.")} onRetry={() => void current.refetch()} />
            </Group>
        );
    }

    const join: OrgJoin = (set.isPending && set.variables) || current.data || { policy: "invited", domain: "" };
    const said = orgJoinSaid(join);
    const pick = (policy: OrgJoinPolicy) => {
        set.reset();
        const next = pickJoin(join, policy);
        if ("askDomain" in next) router.push(orgRoutes.domain as never);
        else if (next.save.policy !== join.policy) set.mutate(next.save);
    };
    const changes = mayChange !== false;

    return (
        <Group label="Who else can join" foot={foot}>
            {current.isPending ? <Reading /> : (
                <Row
                    label={said.who}
                    hint={said.then}
                    value={set.isPending ? "Saving…" : null}
                    onPress={changes ? () => setPicking(true) : undefined}
                    disabled={mayChange === null}
                    last={!(join.policy === "domain" && changes)}
                />
            )}
            {!current.isPending && join.policy === "domain" && changes ? (
                <Row label={`Addresses ending @${join.domain}`} value="Change" onPress={() => router.push(orgRoutes.domain as never)} disabled={mayChange === null} last />
            ) : null}
            {mayChange === false && !current.isPending ? <Say>Only an owner of {org.name} can change this.</Say> : null}
            {set.isError ? (
                <Say tone="bad">{isForbidden(set.error) ? `Only an owner of ${org.name} can change who can join.` : problemOf(set.error, "That could not be saved. Try again.")}</Say>
            ) : null}
            <ChoiceSheet
                open={picking}
                title="Who else can join"
                choices={orgJoinChoices(join.domain).map((choice) => ({ value: choice.policy, label: choice.who, hint: choice.then }))}
                value={join.policy}
                onPick={pick}
                onClose={() => setPicking(false)}
            />
        </Group>
    );
}

/** Ask somebody in by email, with a role (the web's invite row, as a screen
 *  of its own so the keyboard never covers it). When the server sent no
 *  email, the link to hand over is shown, ready to copy. */
export function InviteScreen() {
    return (
        <WithOrg title="Invite someone" back="People">
            {(org) => <Invite org={org} />}
        </WithOrg>
    );
}

function Invite({ org }: { org: Org }) {
    const people = useOrgPeople(org.id);
    const invite = useInvite(org.id);
    const local = useDeployment() === "local";
    const [email, setEmail] = useState("");
    const [role, setRole] = useState<Role>("ORG_MEMBER");
    const [problem, setProblem] = useState<string | null>(null);
    const [sent, setSent] = useState<Invitation | null>(null);
    const choices = rolesToGive(people.myRole);
    const note = `They join ${org.name} with the role you choose.`;

    if (!people.answered) {
        return (
            <SettingsScreen title="Invite someone" note={note} back="People">
                {people.members.isError ? <ReadProblem said={problemOf(people.members.error, "Who you are here could not be read.")} onRetry={() => void people.members.refetch()} /> : <Reading />}
            </SettingsScreen>
        );
    }
    if (!people.manage) {
        return (
            <SettingsScreen title="Invite someone" back="People">
                <Say>Only owners and editors of {org.name} can invite people. Ask one of them.</Say>
            </SettingsScreen>
        );
    }

    const send = () => {
        const wrong = inviteWrong(email, people.people, people.pending);
        if (wrong) { setProblem(wrong); return; }
        setProblem(null);
        setSent(null);
        invite.mutate({ email: email.trim(), role }, {
            onSuccess: (made) => { setSent(made); setEmail(""); },
            onError: (error) => setProblem(isForbidden(error) ? `You may not invite people to ${org.name}.` : problemOf(error, "That invitation was not sent. Try again.")),
        });
    };
    const unsent = sent ? unsentInvitation(sent) : null;

    return (
        <SettingsScreen title="Invite someone" note={note} back="People">
            <Field
                label="Email address"
                value={email}
                onChange={(next) => { setEmail(next); setProblem(null); }}
                placeholder="name@example.com"
                keyboardType="email-address"
                autoFocus
                error={problem}
                onSubmit={send}
            />
            <Group>
                <ChoiceRow label="Role" hint={roleBlurb(role)} title="Joins as" choices={choices} value={role} onPick={setRole} last />
            </Group>
            <View style={{ flexDirection: "row" }}>
                <Button title={invite.isPending ? "Sending…" : "Send invitation"} grow busy={invite.isPending} disabled={!email.trim()} onPress={send} />
            </View>
            {sent && unsent ? (
                <View style={{ gap: 8 }}>
                    <Say>{unsent.said}</Say>
                    {unsent.link ? <Copyable value={unsent.link} label={`invitation link for ${unsent.to}`} mono /> : null}
                    <OnlyOpensHere link={unsent.link} local={local} />
                </View>
            ) : sent ? (
                <Say tone="done">
                    {sent.emailed ? `Invited ${sent.email ?? "them"}. They were emailed a link to join.` : `Invited ${sent.email ?? "them"}.`}
                </Say>
            ) : null}
        </SettingsScreen>
    );
}

/** The domain the middle rule lets in: typed here, its own screen so the
 *  keyboard has room. Saving it also sets the rule to it, which is how the
 *  rule is chosen in the first place. Owners only. */
export function JoinDomainScreen() {
    return (
        <WithOrg title="Your email domain" back="People">
            {(org) => <JoinDomain org={org} />}
        </WithOrg>
    );
}

function JoinDomain({ org }: { org: Org }) {
    const router = useRouter();
    const people = useOrgPeople(org.id);
    const current = useOrgJoin(org.id);
    const set = useSetOrgJoin(org.id);
    const [typed, setTyped] = useState<string | null>(null);
    const [wrong, setWrong] = useState<string | null>(null);
    const value = typed ?? current.data?.domain ?? "";
    const note = `Anyone with an address at this domain can join ${org.name} without an invitation, once they have proved the address.`;

    if (people.answered && !canSetJoinPolicy(people.myRole)) {
        return (
            <SettingsScreen title="Your email domain" back="People">
                <Say>Only an owner of {org.name} can change who can join.</Say>
            </SettingsScreen>
        );
    }

    const save = () => {
        const problem = domainProblem(value);
        if (problem) { setWrong(problem); return; }
        setWrong(null);
        set.mutate({ policy: "domain", domain: cleanDomain(value) }, { onSuccess: () => router.back() });
    };

    return (
        <SettingsScreen title="Your email domain" note={note} back="People">
            {current.isPending ? <Reading /> : (
                <Field
                    label="The domain your addresses end in"
                    value={value}
                    onChange={(next) => { setTyped(next); setWrong(null); }}
                    placeholder="example.com"
                    keyboardType="url"
                    autoFocus
                    error={wrong}
                    hint="Just the part after the @."
                    onSubmit={save}
                />
            )}
            {set.isError ? (
                <Say tone="bad">{isForbidden(set.error) ? `Only an owner of ${org.name} can change who can join.` : problemOf(set.error, "That could not be saved. Try again.")}</Say>
            ) : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Cancel" kind="outline" grow onPress={() => router.back()} />
                <Button title={set.isPending ? "Saving…" : "Save"} grow busy={set.isPending} disabled={!value.trim() || current.isPending} onPress={save} />
            </View>
        </SettingsScreen>
    );
}
