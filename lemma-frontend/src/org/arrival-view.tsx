import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import type { Invitation, Org } from "@/data";
import { ArrowRightIcon, CheckIcon, GlobeIcon, LockIcon, OrgIcon, UserIcon } from "@/ui/icons";
import { LemmaLogo } from "@/ui/icons";
import { AI_MATES, ORG } from "@/copy";
import { canOpenToDomain, domainOf, personalNameFor, teamNameFor } from "./arrival";

/** The first morning.
 *
 *  Deliberately not "You are not in an organization yet, ask somebody to add
 *  you, here is a link to the platform". Two thirds of that is wrong at once:
 *  the app can make an organization itself, and — worse — somebody may already
 *  have added you. `invitations.listMine` is what knows, and without asking it
 *  a person who has already been invited is told to go and ask to be invited.
 *
 *  Three rungs, closed to open, and the order is the point. An invitation is
 *  the most specific thing anyone can know about you, so it goes first. A
 *  domain match is next, because the company being here already is a better
 *  answer than making a second one beside it. Making one is last, and only
 *  then is anything a decision.
 */
export function ArrivalView({
    email,
    name,
    onEntered,
}: {
    email: string | null;
    name: string | null;
    /** Where they landed, when the way in named somewhere. An invitation can
     *  carry a pod, and arriving on the teammate somebody brought you in for
     *  is the whole difference between being welcomed and being dropped into
     *  the middle of a rail of strangers. */
    onEntered: (podId: string | null) => void;
}) {
    const queryClient = useQueryClient();

    const invitations = useQuery({ queryKey: ["my-invitations"], queryFn: () => source.myInvitations() });
    const suggested = useQuery({ queryKey: ["suggested-orgs"], queryFn: () => source.suggestedOrgs() });

    /* One mutation for three verbs, because the three do the same thing: they
       end with this person belonging somewhere, and the only way the app finds
       that out is by asking for the list again. */
    const [busy, setBusy] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const enter = useMutation({
        mutationFn: async (act: { id: string; run: () => Promise<unknown>; land: string | null }) => {
            setBusy(act.id);
            await act.run();
            return act.land;
        },
        onSuccess: (land) => {
            onEntered(land ?? null);
            return queryClient.invalidateQueries({ queryKey: ["orgs"] });
        },
        onError: (failure) => {
            setBusy(null);
            setProblem(failure instanceof Error ? failure.message : "Couldn’t complete organization setup. Try again.");
        },
    });
    const go = (id: string, run: () => Promise<unknown>, land?: string | null) => {
        setProblem(null);
        enter.mutate({ id, run, land: land ?? null });
    };

    const waiting = invitations.isPending || suggested.isPending;
    const invites = useMemo(() => invitations.data ?? [], [invitations.data]);
    /* An organization that already invited you is not also a suggestion. Both
       endpoints can name it — one because somebody put your name down, the
       other because your domain is allowed in — and two rows for one place,
       with two different verbs, asks the reader to work out whether they are
       the same door. The invitation wins: it is the more specific fact, and it
       is the one that can carry a teammate. */
    const matches = useMemo(
        () => (suggested.data ?? []).filter((org) => !invites.some((invite) => invite.orgId === org.id)),
        [suggested.data, invites],
    );

    return (
        <div className="arrival-page">
            {/* Top left, where a product's mark goes. Centred over the
                heading it read as a splash screen, and this is a screen you
                do something on. */}
            <header className="arrival-page__bar"><LemmaLogo /></header>
            <div className="arrival">
                {waiting ? (
                    <p role="status">Looking for your {ORG}…</p>
                ) : (
                    <>
                        {/* The heading is the question the two cards answer.
                            It said "First, a place to work" — which made the
                            organization the point, when the organization is
                            the thing in the way of the point, and "a place to
                            work" could have meant a desk. */}
                        <h1>{invites.length || matches.length ? "You’ve been invited" : "Who will you be working with?"}</h1>
                        <p className="arrival__lede">
                            {invites.length
                                ? "Accept an invitation to join your team."
                                : matches.length
                                    ? "You can join with your verified work email."
                                    : "This decides who else can see your " + AI_MATES + " and the work they do."}
                        </p>

                        {invites.map((invite) => (
                            <InviteRow
                                key={invite.id}
                                invite={invite}
                                busy={busy === invite.id}
                                onAccept={() => go(invite.id, () => source.acceptInvitation(invite.id), invite.podId)}
                            />
                        ))}

                        {matches.map((org) => (
                            <MatchRow
                                key={org.id}
                                org={org}
                                domain={domainOf(email)}
                                busy={busy === org.id}
                                onJoin={() => go(org.id, () => source.joinOrg(org.id))}
                            />
                        ))}

                        <MakeOne
                            /* Keyed on the domain, because both of this
                               panel's defaults are read from it once at mount:
                               which kind is preselected, and what the name box
                               is prefilled with. The account resolves on its
                               own schedule and may well land after the
                               organization list, and a company arriving a
                               moment late would otherwise be offered "just me"
                               with an empty name — the two things the domain
                               exists to get right. */
                            key={domainOf(email)}
                            email={email}
                            name={name}
                            /* Folded away behind a link once there is something
                               better on the page. Offering "make a new one"
                               with the same weight as "join the one your
                               colleagues are already in" is how an org ends up
                               with one person in it. */
                            secondary={invites.length > 0 || matches.length > 0}
                            busy={busy === "make"}
                            onMake={(wanted) => go("make", () => source.createOrg(wanted))}
                        />

                        {problem && <p className="arrival__problem">{problem}</p>}
                    </>
                )}
            </div>
        </div>
    );
}

/** An invitation, said as what it is.
 *
 *  It names the teammate when it has one — `pod_name` and `pod_description`
 *  ride along on the invitation — because "you were invited to Acme" and "you
 *  were invited to Acme, to work with Marketing" are different amounts of
 *  knowing where you are about to land. */
function InviteRow({ invite, busy, onAccept }: { invite: Invitation; busy: boolean; onAccept: () => void }) {
    return (
        <div className="arrival__row">
            <span className="arrival__mark-tile"><OrgIcon size={18} /></span>
            <div className="arrival__said">
                <b>{invite.orgName}</b>
                <span>
                    {invite.podName
                        ? "Invited to work with " + invite.podName
                        : "Invited as " + invite.role.replace("ORG_", "").toLowerCase()}
                </span>
                {invite.podAbout && <em>{invite.podAbout}</em>}
            </div>
            <button className="btn btn--primary" onClick={onAccept} disabled={busy}>
                {busy ? "Coming in…" : "Accept"} {!busy && <ArrowRightIcon size={14} />}
            </button>
        </div>
    );
}

function MatchRow({ org, domain, busy, onJoin }: { org: Org; domain: string; busy: boolean; onJoin: () => void }) {
    return (
        <div className="arrival__row">
            <span className="arrival__mark-tile"><GlobeIcon size={18} /></span>
            <div className="arrival__said">
                <b>{org.name}</b>
                <span>{domain ? "Anyone with an @" + domain + " address may join" : "Open to your email domain"}</span>
            </div>
            <button className="btn btn--primary" onClick={onJoin} disabled={busy}>
                {busy ? "Joining…" : "Join"} {!busy && <ArrowRightIcon size={14} />}
            </button>
        </div>
    );
}

/** Just you, or your team.
 *
 *  The question is asked rather than inferred, and the domain decides only the
 *  default and what the team option is allowed to do. A shared mailbox
 *  provider cannot have the open policy at all: `EMAIL_DOMAIN` on `gmail.com`
 *  admits every Gmail address there is, and a person choosing "my team" on a
 *  Gmail address means "I will invite them", not "let the internet in".
 *
 *  What is chosen here is what makes the *next* person's arrival work. A team
 *  workspace left open to its own domain is what puts the rung above this one
 *  on their screen instead of this one. */
function MakeOne({
    email,
    name,
    secondary,
    busy,
    onMake,
}: {
    email: string | null;
    name: string | null;
    secondary: boolean;
    busy: boolean;
    onMake: (wanted: { name: string; emailDomain?: string; derived?: boolean }) => void;
}) {
    const domain = domainOf(email);
    const canOpen = canOpenToDomain(email);
    const suggestion = useMemo(() => teamNameFor(email), [email]);
    /* A company address is a reason to expect colleagues; a Gmail one is not.
       It is a preselection, not an answer. */
    const [kind, setKind] = useState<"personal" | "team">(canOpen ? "team" : "personal");
    const [called, setCalled] = useState(suggestion);
    const [open, setOpen] = useState(!secondary);

    if (!open) {
        return (
            <button className="arrival__instead" onClick={() => setOpen(true)}>
                Or start {"an " + ORG} of your own
            </button>
        );
    }

    const personalName = personalNameFor(name);
    const ready = kind === "personal" || called.trim().length > 0;

    return (
        <div className="arrival__make">
            {secondary && <h3>Start one of your own</h3>}

            <div className="arrival__kinds">
                <button
                    className="arrival__kind"
                    aria-pressed={kind === "personal"}
                    onClick={() => setKind("personal")}
                >
                    <UserIcon size={18} />
                    <b>Just me</b>
                    <span>Yours alone. You can bring people in later.</span>
                </button>
                <button
                    className="arrival__kind"
                    aria-pressed={kind === "team"}
                    onClick={() => setKind("team")}
                >
                    <OrgIcon size={18} />
                    <b>My team</b>
                    <span>Create AI teammates for your team and choose who can access each one.</span>
                </button>
            </div>

            {kind === "team" ? (
                <>
                    <label className="arrival__field">
                        <span>What is your team called?</span>
                        <input
                            value={called}
                            placeholder="Acme"
                            autoFocus
                            onChange={(event) => setCalled(event.target.value)}
                            onKeyDown={(event) => { if (event.key === "Enter" && ready) onMake(teamWanted()); }}
                        />
                    </label>
                    {/* One element beside the mark, not a run of text and
                        tags: every text node in a flex row is its own flex
                        item, which laid this sentence out in columns. */}
                    <p className="arrival__note">
                        {canOpen ? <GlobeIcon size={14} /> : <LockIcon size={14} />}
                        <span>
                            {canOpen
                                /* The one fact worth saying: what happens to
                                   the next person from this company. The old
                                   version of the other branch explained why
                                   gmail.com cannot have the open policy, which
                                   is a decision nobody asked about, in three
                                   lines, on somebody's first screen. */
                                ? <>Anyone with an <code>@{domain}</code> address can join without being invited.</>
                                : <>You add people by inviting them.</>}
                        </span>
                    </p>
                </>
            ) : (
                <p className="arrival__note">
                    <LockIcon size={14} />
                    {/* The name only when there is a real one to say. It was
                        announcing "deepakjha0196+99's Personal" to somebody
                        who had not been asked for a name and would not have
                        chosen that one. */}
                    <span>
                        Private to you.{" "}
                        {personalName === "Personal" ? "You can invite people later." : <>It will be called <b>{personalName}</b>.</>}
                    </span>
                </p>
            )}

            <button
                className="btn btn--primary"
                disabled={busy || !ready}
                onClick={() => onMake(kind === "team" ? teamWanted() : {
                    name: personalName,
                    /* A name nobody typed. `resolve_name_conflicts` exists for
                       exactly this: a second Alice should get a workspace, not
                       a 409 about a name she never chose. */
                    derived: true,
                })}
            >
                {busy ? "Setting it up…" : kind === "team" ? "Create " + (called.trim() || ORG) : "Create my " + ORG}
            </button>
        </div>
    );

    function teamWanted() {
        return { name: called.trim(), ...(canOpen ? { emailDomain: domain } : {}) };
    }
}

/** What a finished arrival looks like, for the moment between the list coming
 *  back and the shell taking over. */
export function ArrivalDone() {
    return (
        <div className="screen"><div className="screen__inner arrival">
            <p className="arrival__mark"><CheckIcon size={26} /></p>
            <p role="status">Opening your workspace…</p>
        </div></div>
    );
}
