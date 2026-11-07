import { useState } from "react";
import { useRouter } from "expo-router";
import { useLemma, useMe, useOrg } from "../../../data/hooks";
import { PersonFace } from "../../../ui/face";
import { Button } from "../../../ui/press";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { ChoiceSheet, Group, ReadProblem, Reading, Row, RowIcon, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { useAdmit, useJoinPolicy, useJoinRequests, useMayManage, usePendingInvites, usePodMembers, useSetJoinPolicy } from "./hooks";
import { accessOf, askedLine, askingLine, sayJoinRefusal } from "./model";
import { peopleRoutes } from "./routes";
import { askerName, joinChoices, joinSaid, type JoinPolicy } from "@web/data/joining";

export { AddPeopleScreen } from "./add";

/** Who has access to a Lemma, and how somebody else gets it: the web's
 *  People part of About (the roster with Add people, the door, and the
 *  queue at it), as one screen. Who is in, who is asking, who could. */
export function PeopleScreen({ podId }: PartProps) {
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "this Lemma";
    const part = lemmaPart("people")!;
    const { org } = useOrg();
    return (
        <SettingsScreen title={part.title(name)} note={part.note(name)} back={lemma?.name ?? "Settings"}>
            <Roster podId={podId} name={name} orgId={org?.id ?? null} />
            <AtTheDoor podId={podId} name={name} />
            <WhoCanJoin podId={podId} name={name} org={org?.name ?? ""} />
        </SettingsScreen>
    );
}

/** Everyone with access and what each may do, then the invitations still
 *  waiting here, so nobody invites an address twice. The way to add more
 *  is offered only to whoever the server lets. */
function Roster({ podId, name, orgId }: { podId: string; name: string; orgId: string | null }) {
    const router = useRouter();
    const me = useMe();
    const may = useMayManage(podId);
    const members = usePodMembers(podId);
    const pending = usePendingInvites(podId, orgId, may.allowed);
    const mine = (me.data?.email ?? "").toLowerCase();
    const people = members.data ?? [];
    const invites = pending.data ?? [];
    const rows = people.length + invites.length;
    return (
        <Group label="With access" foot={may.answered && !may.allowed ? `Only admins of ${name} can add people.` : undefined}>
            {may.allowed ? (
                <Row label="Add people" role="link" glyph={<RowIcon name="plus" />} last={rows === 0 && !members.isPending} onPress={() => router.push(peopleRoutes.add(podId) as never)} />
            ) : null}
            {members.isPending ? <Reading /> : null}
            {members.isError ? (
                <ReadProblem said={isForbidden(members.error) ? `You may not see who has access to ${name}.` : `Couldn’t load who has access to ${name}.`} onRetry={() => void members.refetch()} />
            ) : null}
            {people.map((person, index) => {
                const you = Boolean(mine) && (person.email ?? "").toLowerCase() === mine;
                return (
                    <Row
                        key={person.id}
                        glyph={<PersonFace name={person.name} size={28} />}
                        label={you ? `${person.name} · you` : person.name}
                        hint={person.email && person.email !== person.name ? person.email : null}
                        value={accessOf(person)}
                        last={index === rows - 1}
                    />
                );
            })}
            {invites.map((invite, index) => (
                <Row key={invite.id} glyph={<RowIcon name="mail" />} label={invite.email} hint="Has not accepted yet" value="Invited" last={people.length + index === rows - 1} />
            ))}
        </Group>
    );
}

/** Who is waiting to be let in, for whoever can let them. A refusal to list
 *  them is the ordinary case and draws nothing; an empty queue draws
 *  nothing either, since the door below already says who may knock. */
function AtTheDoor({ podId, name }: { podId: string; name: string }) {
    const may = useMayManage(podId);
    const waiting = useJoinRequests(podId);
    const admit = useAdmit(podId);
    if (isForbidden(waiting.error)) return null;
    if (waiting.isError) {
        return (
            <Group label="Asking to join">
                <ReadProblem said={`Couldn’t load who is asking to join ${name}.`} onRetry={() => void waiting.refetch()} />
            </Group>
        );
    }
    const queue = waiting.data ?? [];
    if (queue.length === 0) return null;
    return (
        /* There is no turning an ask away: the server keeps a refused status
           and no way to set it, so the queue says what is true instead of
           offering a Decline that would always fail. */
        <Group label={askingLine(queue.length, name)} foot="An ask stays here until it is approved.">
            {queue.map((request, index) => {
                const busy = admit.isPending && admit.variables?.id === request.id;
                return (
                    <Row
                        key={request.id}
                        glyph={<PersonFace name={askerName(request)} size={28} />}
                        label={askerName(request)}
                        hint={askedLine(request) || null}
                        needs
                        last={index === queue.length - 1}
                        end={may.allowed ? (
                            <Button title={busy ? "Approving…" : "Approve"} kind="outline" busy={busy} disabled={admit.isPending} onPress={() => admit.mutate(request)} />
                        ) : null}
                    />
                );
            })}
            {admit.isError ? (
                <Say tone="bad">{isForbidden(admit.error) ? `You may not let people into ${name}.` : problemOf(admit.error, "That could not be done. Try again.")}</Say>
            ) : null}
        </Group>
    );
}

/** Who may let themselves in, said as what happens rather than as a rule's
 *  name. Shown to everyone; moved only by whoever may change who has access. */
function WhoCanJoin({ podId, name, org }: { podId: string; name: string; org: string }) {
    const may = useMayManage(podId);
    const current = useJoinPolicy(podId);
    const set = useSetJoinPolicy(podId);
    const [open, setOpen] = useState(false);
    if (current.isError) {
        return (
            <Group label="Who else can join">
                <ReadProblem said={`Couldn’t load who can join ${name}.`} onRetry={() => void current.refetch()} />
            </Group>
        );
    }
    /* What is being saved, while it is: the door on screen and the door in
       force should not disagree for the half second a write takes. */
    const policy: JoinPolicy = set.isPending && set.variables ? set.variables : (current.data ?? "invited");
    const said = joinSaid(policy, org);
    const choices = joinChoices(org).map((choice) => ({ value: choice.policy, label: choice.who, hint: choice.then }));
    const refused = set.isError
        ? sayJoinRefusal(problemOf(set.error, isForbidden(set.error) ? `You may not change who can join ${name}.` : "That could not be saved. Try again."), org)
        : null;
    return (
        <Group label="Who else can join">
            {current.isPending ? <Reading /> : (
                <Row
                    label={said.who}
                    hint={said.then}
                    last
                    onPress={may.allowed && !set.isPending ? () => { set.reset(); setOpen(true); } : undefined}
                />
            )}
            {refused ? <Say tone="bad">{refused}</Say> : null}
            <ChoiceSheet
                open={open}
                title="Who else can join"
                choices={choices}
                value={policy}
                onPick={(next) => { if (next !== policy) set.mutate(next); }}
                onClose={() => setOpen(false)}
            />
        </Group>
    );
}
