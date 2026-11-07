import { useState } from "react";
import { View } from "react-native";
import { useLemma, useOrg } from "../../../data/hooks";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useOrgMembers, usePodActions } from "../access/hooks";
import type { PartProps } from "../types";
import { ChoiceRow, Copyable, Field, Group, ReadProblem, Reading, Row, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { linkOnlyOpensHere, unsentInvitation } from "@web/org/membership";
import { useAddPerson, useInvitePerson, useMayManage, usePendingInvites, usePodMembers } from "./hooks";
import { ACCESS_CHOICES, addPlan, candidatesFrom, submitProblem, type Candidate, type SentInvite } from "./model";

/** Adding people to a Lemma: one box for both ways in. Somebody already in
 *  the organization is given access at once; a new address is sent an
 *  organization invitation that names this Lemma and the access it gives,
 *  so accepting joins both and lands them here (the web's Add people). */
export function AddPeopleScreen({ podId }: PartProps) {
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "this Lemma";
    const { org } = useOrg();
    const orgName = org?.name ?? "";
    const may = useMayManage(podId);
    const actions = usePodActions(podId);
    const members = usePodMembers(podId);
    const orgMembers = useOrgMembers(may.allowed ? org?.id : null);
    const pending = usePendingInvites(podId, org?.id, may.allowed);
    const add = useAddPerson(podId);
    const invite = useInvitePerson(podId, org?.id);

    const [query, setQuery] = useState("");
    const [role, setRole] = useState(ACCESS_CHOICES[0].value);
    const [problem, setProblem] = useState<string | null>(null);
    const [added, setAdded] = useState<string | null>(null);
    const [sent, setSent] = useState<SentInvite | null>(null);

    const plan = addPlan({
        query,
        candidates: candidatesFrom(orgMembers.data ?? [], members.data ?? []),
        people: members.data ?? [],
        pending: pending.data ?? [],
        orgRead: orgMembers.isSuccess && members.isSuccess,
        lemma: name,
        org: orgName,
    });
    const busy = add.isPending || invite.isPending;
    const unsent = sent ? unsentInvitation({ email: sent.email, accept_url: sent.acceptUrl, emailed: sent.emailed }) : null;

    const clear = () => { setProblem(null); setAdded(null); setSent(null); };
    const give = (candidate: Candidate) => {
        if (busy) return;
        clear();
        add.mutate({ orgMemberId: candidate.id, role }, {
            onSuccess: () => { setQuery(""); setAdded(candidate.name); },
            onError: (error) => setProblem(isForbidden(error) ? `You may not add people to ${name}.` : problemOf(error, "Couldn’t add this person. Try again.")),
        });
    };
    const send = (email: string) => {
        if (busy || !org) return;
        clear();
        invite.mutate({ email, role }, {
            onSuccess: (made) => { setQuery(""); setSent(made); },
            onError: (error) => setProblem(isForbidden(error) ? `You may not invite people to ${orgName || "this organization"}.` : problemOf(error, "That invitation was not sent. Try again.")),
        });
    };
    const submit = () => {
        if (busy) return;
        if (plan.action === "add" && plan.exact) give(plan.exact);
        else if (plan.action === "invite") send(query.trim());
        else setProblem(submitProblem(plan, query));
    };

    if (may.answered && !may.allowed) {
        return (
            <SettingsScreen title="Add people" back="People">
                <Say>Only admins of {name} can add people.</Say>
            </SettingsScreen>
        );
    }
    if (!may.answered) {
        return (
            <SettingsScreen title="Add people" back="People">
                {actions.isError ? (
                    <ReadProblem said={`Couldn’t check whether you may add people to ${name}.`} onRetry={() => void actions.refetch()} />
                ) : <Reading />}
            </SettingsScreen>
        );
    }

    const line = problem ?? plan.hint;
    return (
        <SettingsScreen title="Add people" note={`Somebody in ${orgName || "the organization"} gets access at once. A new address is sent an invitation to ${orgName || "the organization"} that lands them in ${name}.`} back="People">
            <View style={{ gap: 12 }}>
                <Field
                    label="Email address or name"
                    value={query}
                    onChange={(next) => { setQuery(next); setProblem(null); }}
                    placeholder="Add by email"
                    keyboardType="email-address"
                    autoFocus
                    onSubmit={submit}
                />
                <ChoiceRow
                    label="Access"
                    title={`Access to ${name}`}
                    choices={ACCESS_CHOICES}
                    value={role}
                    onPick={setRole}
                    last
                />
                <Button
                    title={invite.isPending ? "Sending…" : add.isPending ? "Adding…" : plan.action === "invite" ? "Invite" : "Add"}
                    busy={busy}
                    disabled={!plan.action}
                    onPress={submit}
                />
                {line ? <Say tone={problem ? "bad" : "quiet"}>{line}</Say> : null}
                {added ? <Say tone="done">{added} can now open {name}.</Say> : null}
                {sent && !unsent ? <Say tone="done">Invited {sent.email}. They will land in {name} once they accept.</Say> : null}
                {unsent ? (
                    <View style={{ gap: 6 }}>
                        <Say>{unsent.said}</Say>
                        {unsent.link ? <Copyable value={unsent.link} label={`invitation link for ${unsent.to}`} mono /> : null}
                        {linkOnlyOpensHere(unsent.link) ? (
                            <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>
                                This link opens only on the computer running Lemma, until sharing is turned on there.
                            </Text>
                        ) : null}
                    </View>
                ) : null}
            </View>

            <Candidates
                typed={plan.typed}
                shown={plan.shown}
                orgName={orgName}
                reading={orgMembers.isPending}
                failed={orgMembers.isError}
                adding={add.isPending ? add.variables?.orgMemberId ?? null : null}
                disabled={busy}
                onAdd={give}
            />
        </SettingsScreen>
    );
}

/** Who in the organization could be added: all of them under the box, or
 *  the ones matching what is typed, which replaces the list rather than
 *  being buried under it. A tap adds at the access chosen above. */
function Candidates({ typed, shown, orgName, reading, failed, adding, disabled, onAdd }: {
    typed: string;
    shown: readonly Candidate[];
    orgName: string;
    reading: boolean;
    failed: boolean;
    adding: string | null;
    disabled: boolean;
    onAdd: (candidate: Candidate) => void;
}) {
    if (reading) return <Group label={`Also in ${orgName || "this organization"}`}><Reading label="Reading your organization" /></Group>;
    if (failed) return <Group label="Also in this organization"><Say tone="bad">Couldn’t load the organization’s people.</Say></Group>;
    if (shown.length === 0) return null;
    return (
        <Group label={typed ? "Matching" : `Also in ${orgName || "this organization"}`}>
            {shown.map((candidate, index) => (
                <Row
                    key={candidate.id}
                    label={candidate.name}
                    hint={[candidate.email !== candidate.name ? candidate.email : "", candidate.orgRole].filter(Boolean).join(" · ") || null}
                    disabled={disabled}
                    last={index === shown.length - 1}
                    onPress={() => onAdd(candidate)}
                    end={<Text size={15} weight="medium">{adding === candidate.id ? "Adding…" : "Add"}</Text>}
                />
            ))}
        </Group>
    );
}
