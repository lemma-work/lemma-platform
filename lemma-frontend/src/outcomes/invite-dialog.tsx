"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueries, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { MANAGE_MEMBERS, POD_ROLES, saidAbout, source, type Org, type Pod } from "@/data";
import { copyText } from "@/desktop/clipboard";
import { isLandingPreview } from "@/marketing/preview-mode";
import {
    ROLES as ORG_ROLES, alreadyKnown, canSetRole, inviteProblem, memberName, unsentInvitation,
    type Member as OrgMember, type Role,
} from "@/org/membership";
import { lemma } from "@/session/client";
import { Modal } from "@/shell/modal";
import { Avatar, Icon } from "./parts";

/* ── what the People page and its dialogs share ───────────────────────── */

interface Listish {
    items?: unknown[];
    next_page_token?: string | null;
}

export function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    return (value as Listish | undefined)?.items ?? [];
}

/** An invitation as its organization's owners and editors read it. */
export interface OrgInvite {
    id: string;
    email?: string | null;
    role?: string | null;
    status?: string | null;
    created_at?: string | null;
    expires_at?: string | null;
    accept_url?: string | null;
    emailed?: boolean | null;
    pod_id?: string | null;
    pod_role?: string | null;
}

/** What each of a Lemma's roles lets somebody do there, in the words the
 *  choice cards use. The roles and their order are `POD_ROLES`. */
const LEMMA_ROLE_SAYS: Record<string, string> = {
    POD_ADMIN: "Everything, including who’s in it and deleting it.",
    POD_EDITOR: "Builds and changes its apps, tables, automations and skills.",
    POD_USER: "Chats with it, runs its automations, adds and edits rows. Can’t change how it’s built.",
    POD_VIEWER: "Sees everything in it. Changes nothing.",
};

export const LEMMA_ROLES = POD_ROLES.map((role) => ({ value: role.value as string, label: role.label as string, blurb: LEMMA_ROLE_SAYS[role.value] ?? role.can }));

/** The role somebody is given in a Lemma unless the person adding them
 *  picks another: using it, the least that answers "let them in". */
export const LEMMA_ROLE_FIRST = "POD_USER";

/** A Lemma role as the picker says it, or the wire's word tidied. */
export function lemmaRoleSaid(role: string | null | undefined): string {
    const known = LEMMA_ROLES.find((entry) => entry.value === role);
    if (known) return known.label;
    const tidied = String(role ?? "").replace(/^POD_/, "").replaceAll("_", " ").toLowerCase();
    return tidied ? tidied.charAt(0).toUpperCase() + tidied.slice(1) : "Member";
}

/** Every member of the organization, page by page, bounded so a runaway
 *  cursor cannot loop for ever. The same walk as `AddPeople`'s, under the
 *  same key, so the page and its dialogs read the roster once. */
async function everyOrgMember(orgId: string): Promise<{ items: unknown[] }> {
    const all: unknown[] = [];
    let token: string | undefined;
    for (let page = 0; page < 50; page++) {
        const listed = (await lemma().organizations.members.list(orgId, { limit: 100, pageToken: token })) as Listish;
        all.push(...itemsOf(listed));
        token = listed.next_page_token ?? undefined;
        if (!token) break;
    }
    return { items: all };
}

export interface OrgRoster {
    me: UseQueryResult<{ id?: string; email?: string | null }>;
    members: UseQueryResult<{ items: unknown[] }>;
    invitations: UseQueryResult<unknown>;
    /** The organization's members, as the server listed them. */
    list: OrgMember[];
    /** Every invitation the reader may see, whatever its state. */
    invites: OrgInvite[];
    /** What the reader is here: only findable by finding them in the list. */
    myRole: string | null;
}

/** Who is in the organization and who has been asked, for the reader.
 *
 *  Off in the sample, which has no organization behind it to have members
 *  of. Invitations are listed only for owners and editors; anybody else is
 *  refused, which is the ordinary case and not a fault. */
export function useOrgRoster(orgId: string, enabled: boolean): OrgRoster {
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current() as Promise<{ id?: string; email?: string | null }>,
        enabled,
        staleTime: 5 * 60_000,
    });
    const members = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: () => everyOrgMember(orgId),
        enabled,
    });
    const invitations = useQuery({
        queryKey: ["org-invitations", orgId],
        queryFn: () => lemma().organizations.invitations.list(orgId, { limit: 100 }),
        enabled,
        retry: false,
    });
    const list = useMemo(() => itemsOf(members.data) as OrgMember[], [members.data]);
    const invites = useMemo(() => itemsOf(invitations.data) as OrgInvite[], [invitations.data]);
    const myRole = list.find((member) => Boolean(me.data?.id) && member.user_id === me.data?.id)?.role ?? null;
    return { me, members, invitations, list, invites, myRole };
}

export interface PodManaging {
    allowed: boolean;
    answered: boolean;
}

/** Whether the reader may add, re-role and take people out of each Lemma.
 *
 *  Asked of the server (`pod.member.manage`), under the key `AddPeople`
 *  asks it, rather than guessed from a role. Unknown counts as no: nothing
 *  is offered that the server has not said it will allow. The landing
 *  preview answers yes, as it does there. */
export function usePodManagers(pods: readonly Pod[]): Map<string, PodManaging> {
    const preview = isLandingPreview();
    const live = source.label === "live";
    const combine = useCallback(
        (results: UseQueryResult<{ actions?: string[] }>[]) => new Map(pods.map((pod, index) => {
            const result = results[index];
            if (preview) return [pod.id, { allowed: true, answered: true }];
            return [pod.id, { allowed: (result?.data?.actions ?? []).includes(MANAGE_MEMBERS), answered: Boolean(result?.isSuccess) }];
        })),
        [pods, preview],
    );
    return useQueries({
        queries: pods.map((pod) => ({
            queryKey: ["pod-permissions", pod.id],
            queryFn: () => lemma(pod.id).podPermissions.me(pod.id) as Promise<{ actions?: string[] }>,
            enabled: live && !preview,
            staleTime: 60_000,
        })),
        combine,
    });
}

/* The invitation keeps no name on the server yet: the name typed when asking
   somebody in lives here, for this visit, keyed by organization and address,
   and fills their row until they join and bring their own. */
const givenNames = new Map<string, string>();

export function rememberName(orgId: string, email: string, name: string): void {
    const said = name.trim();
    if (said) givenNames.set(orgId + "\n" + email.trim().toLowerCase(), said);
}

export function givenName(orgId: string, email: string): string | null {
    return givenNames.get(orgId + "\n" + email.trim().toLowerCase()) ?? null;
}

/* ── pieces ───────────────────────────────────────────────────────────── */

/** Choices as cards: the whole card is the target, a pick is a blue tint,
 *  an edge and a filled dot (kit.css). A card that cannot be picked here
 *  stays, with why in its own words, rather than vanishing. */
function Choices({ label, name, value, options, onChange }: {
    label: string;
    name: string;
    value: string;
    options: readonly { value: string; label: string; blurb: string; off?: string | null }[];
    onChange: (value: string) => void;
}) {
    const id = useId();
    return (
        <div className="kfield" role="radiogroup" aria-labelledby={id}>
            <span id={id}>{label}</span>
            <div className="kopts">
                {options.map((option) => (
                    <label key={option.value} className="kopt ox-people-dlg__opt" data-off={option.off ? "" : undefined}>
                        <input
                            type="radio"
                            name={name}
                            value={option.value}
                            checked={value === option.value}
                            disabled={Boolean(option.off)}
                            onChange={() => onChange(option.value)}
                        />
                        <span className="kopt__t">{option.label}</span>
                        <span className="kopt__d">{option.off ?? option.blurb}</span>
                        <span className="kopt__dot" aria-hidden="true" />
                    </label>
                ))}
            </div>
        </div>
    );
}

/** An invitation nobody was emailed: the link is the invitation, so it is
 *  shown with a way to copy it, and a copy that fails says so. */
function NotEmailed({ said, link, to }: { said: string; link: string | null; to: string }) {
    const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
    return (
        <div className="ox-people-dlg__sent" role="status">
            <p>{said}</p>
            {link && (
                <div className="ox-people-dlg__link">
                    <input
                        className="kinput"
                        readOnly
                        value={link}
                        aria-label={"Invitation link for " + to}
                        onFocus={(event) => event.target.select()}
                    />
                    <button
                        type="button"
                        className="b b--m b--secondary"
                        onClick={() => { copyText(link).then(() => setState("copied"), () => setState("failed")); }}
                    >
                        <Icon name={state === "copied" ? "check" : "link"} size={16} />
                        {state === "copied" ? "Copied" : "Copy"}
                    </button>
                </div>
            )}
            {state === "failed" && <p className="ox-people-dlg__problem" role="alert">Couldn’t copy it. Select the link and copy it yourself.</p>}
        </div>
    );
}

/** A select drawn as the kit's field, with its chevron. */
function Select({ value, onChange, children, label, id }: {
    value: string;
    onChange: (value: string) => void;
    children: ReactNode;
    label?: string;
    id?: string;
}) {
    return (
        <span className="ox-people-dlg__select">
            <select id={id} className="kinput" value={value} aria-label={label} onChange={(event) => onChange(event.target.value)}>
                {children}
            </select>
            <Icon name="chevron" size={14} />
        </span>
    );
}

/* ── inviting somebody to the organization ────────────────────────────── */

/** Asking somebody into the organization, as one dialog: who they are, what
 *  they can do in it, and, if the reader runs one, a Lemma to start them in.
 *
 *  The role is three cards rather than a select, because the difference
 *  between them is the whole decision and a select hides it. Owner stays on
 *  the list for an editor, who cannot hand it out, with that said on the
 *  card. A Lemma is offered only where the reader may add people to it: the
 *  server bounds an invitation that names a Lemma exactly as it bounds
 *  adding somebody there.
 *
 *  Sent, it closes onto the page, where the new row is the confirmation;
 *  where this server sends no email, it stays open on the link instead,
 *  because otherwise the invitation exists and nobody hears of it. */
export function InviteDialog({ org, pods, onClose, onInvited }: {
    org: Org;
    pods: readonly Pod[];
    onClose: () => void;
    onInvited: (invited: { email: string; name: string; emailed: boolean }) => void;
}) {
    const queryClient = useQueryClient();
    const roster = useOrgRoster(org.id, source.label === "live");
    const choosable = useMemo(() => pods.filter((pod) => !pod.everyone), [pods]);
    const managers = usePodManagers(choosable);
    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [role, setRole] = useState<Role>("ORG_MEMBER");
    const [podId, setPodId] = useState("");
    const [podRole, setPodRole] = useState<string>(LEMMA_ROLE_FIRST);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const [unsent, setUnsent] = useState<ReturnType<typeof unsentInvitation>>(null);
    const nameField = useRef<HTMLInputElement>(null);
    const formId = useId();

    /* After the dialog has taken focus, so the first field is where typing
       lands. */
    useEffect(() => { nameField.current?.focus(); }, []);

    const owner = roster.myRole === "ORG_OWNER";
    const startable = choosable.filter((pod) => owner || managers.get(pod.id)?.allowed);
    const somePodsShut = choosable.some((pod) => !owner && managers.get(pod.id)?.answered && !managers.get(pod.id)?.allowed);
    const pod = startable.find((one) => one.id === podId) ?? null;

    const typed = email.trim();
    const shapeProblem = typed ? inviteProblem(typed) : null;
    const known = typed && !shapeProblem ? alreadyKnown(typed, roster.list, roster.invites) : null;
    const ready = Boolean(typed) && !shapeProblem && !known && !busy && roster.members.isSuccess;

    const roleCards = ORG_ROLES.map((entry) => ({
        value: entry.value,
        label: entry.label,
        blurb: entry.blurb,
        off: roster.members.isSuccess && !canSetRole(roster.myRole, entry.value) ? "Only an owner can make someone an owner." : null,
    }));

    async function send() {
        if (!ready) return;
        setBusy(true);
        setProblem(null);
        try {
            const created = (await lemma().organizations.invitations.invite(org.id, {
                email: typed,
                role: role as never,
                ...(pod ? {
                    pod_id: pod.id,
                    pod_role: podRole,
                    /* Relative, so it resolves against whichever address
                       they open the invitation on. */
                    redirect_uri: "/l/" + encodeURIComponent(pod.id),
                } : {}),
            })) as OrgInvite;
            rememberName(org.id, typed, name);
            await queryClient.invalidateQueries({ queryKey: ["org-invitations", org.id] });
            const notSent = unsentInvitation(created);
            if (notSent) setUnsent(notSent);
            else onInvited({ email: typed, name: name.trim(), emailed: true });
        } catch (error) {
            setProblem(saidAbout(error, "That invitation was not sent."));
        } finally {
            setBusy(false);
        }
    }

    if (unsent) {
        return (
            <Modal
                title={"Invited " + unsent.to}
                subtitle="The invitation is ready. It works for 7 days."
                onClose={() => onInvited({ email: typed, name: name.trim(), emailed: false })}
                footer={<button type="button" className="b b--l b--primary" onClick={() => onInvited({ email: typed, name: name.trim(), emailed: false })}>Done</button>}
            >
                <div className="ox-people-dlg">
                    <NotEmailed said={unsent.said} link={unsent.link} to={unsent.to} />
                </div>
            </Modal>
        );
    }

    return (
        <Modal
            title={"Invite people to " + org.name}
            subtitle="They get an email with a link. It works for 7 days."
            onClose={onClose}
            footer={(
                <>
                    <button type="button" className="b b--l b--secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" form={formId} className="b b--l b--primary" disabled={!ready}>
                        {busy ? "Sending…" : "Send invite"}
                    </button>
                    {problem && <p className="ox-people-note" data-bad="" role="alert">{problem}</p>}
                </>
            )}
        >
            <form id={formId} className="ox-people-dlg" onSubmit={(event) => { event.preventDefault(); void send(); }}>
                <label className="kfield">
                    <span>Name</span>
                    <input
                        ref={nameField}
                        className="kinput"
                        placeholder="Their name"
                        autoComplete="off"
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                    />
                </label>
                <label className="kfield">
                    <span>Email</span>
                    <input
                        className="kinput"
                        type="email"
                        inputMode="email"
                        autoComplete="off"
                        placeholder="name@company.com"
                        value={email}
                        aria-invalid={Boolean(shapeProblem || known) || undefined}
                        onChange={(event) => { setEmail(event.target.value); setProblem(null); }}
                    />
                    {(shapeProblem || known) && typed.includes("@") && <small className="ox-people-dlg__warn">{known ?? shapeProblem}</small>}
                </label>
                <Choices
                    label={"Role in " + org.name}
                    name="org-role"
                    value={role}
                    options={roleCards}
                    onChange={(next) => setRole(next as Role)}
                />
                <div className="kfield">
                    <span id={formId + "-lemma"}>Start them in a Lemma</span>
                    <Select value={podId} onChange={setPodId} label="Start them in a Lemma">
                        <option value="">Not yet</option>
                        {startable.map((one) => <option key={one.id} value={one.id}>{one.name}</option>)}
                    </Select>
                    <small>
                        {startable.length === 0 && somePodsShut
                            ? "Only a Lemma’s admins can start someone in it. You can add them once they’re in."
                            : "Optional. You can add them to more Lemmas once they’re in."}
                    </small>
                </div>
                {pod && (
                    <div className="kfield">
                        <span>What they can do in {pod.name}</span>
                        <Select value={podRole} onChange={setPodRole} label={"What they can do in " + pod.name}>
                            {LEMMA_ROLES.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
                        </Select>
                        <small>{LEMMA_ROLES.find((entry) => entry.value === podRole)?.blurb}</small>
                    </div>
                )}
            </form>
        </Modal>
    );
}

/* ── adding people to one Lemma ───────────────────────────────────────── */

interface Candidate {
    id: string;
    userId: string | null;
    name: string;
    email: string;
    orgRole: string;
}

/** Adding people to one Lemma, in the dialog every Lemma opens.
 *
 *  One way in for both kinds of person, as `AddPeople` has it: somebody
 *  already in the organization is added straight away (`podMembers.add`),
 *  found by typing their name; an address nobody here has is sent an
 *  organization invitation, as a Member, that names this Lemma and its role,
 *  so accepting it lands them here. The button says which it will do, and
 *  the line under it says what follows, before anything is pressed.
 *
 *  Somebody already in this Lemma is still suggested, marked so and not
 *  pickable: leaving them out would read as "not found". Whoever may not add
 *  people here is told so and who can, rather than shown a form the server
 *  will refuse. */
export function AddPeopleDialog({ pod, org, onClose }: { pod: Pod; org: Org; onClose: () => void }) {
    const queryClient = useQueryClient();
    const preview = isLandingPreview();
    const live = source.label === "live";
    const solo = useMemo(() => [pod], [pod]);
    const managing = usePodManagers(solo).get(pod.id) ?? { allowed: false, answered: false };
    const allowed = preview || (live && managing.allowed);
    const roster = useOrgRoster(org.id, live && managing.allowed);
    const preset = useQuery({
        queryKey: ["org-members", org.id, "preview"],
        queryFn: async () => (await import("@/marketing/preview-source")).previewCandidates,
        enabled: preview,
    });

    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [picked, setPicked] = useState<Candidate | null>(null);
    const [role, setRole] = useState<string>(LEMMA_ROLE_FIRST);
    const [lit, setLit] = useState(0);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const [unsent, setUnsent] = useState<ReturnType<typeof unsentInvitation>>(null);
    const nameField = useRef<HTMLInputElement>(null);
    const roles = useRef<HTMLDivElement>(null);
    const listId = useId();
    const formId = useId();

    useEffect(() => { nameField.current?.focus(); }, [allowed]);

    const myId = roster.me.data?.id ?? null;
    const candidates = useMemo<Candidate[]>(() => {
        if (preview) {
            return (preset.data ?? []).map((one) => ({ id: one.id, userId: null, name: one.label, email: one.label, orgRole: one.orgRole }));
        }
        return roster.list
            .filter((member) => !myId || member.user_id !== myId)
            .map((member) => ({
                id: member.id,
                userId: member.user_id ?? null,
                name: memberName(member),
                email: member.user?.email ?? member.email ?? "",
                orgRole: ORG_ROLES.find((entry) => entry.value === member.role)?.label ?? "Member",
            }));
    }, [preview, preset.data, roster.list, myId]);

    const people = useMemo(() => pod.members.filter((member) => member.kind === "person"), [pod.members]);
    const inside = useCallback((candidate: Candidate) => people.some((member) =>
        (candidate.userId && member.userId === candidate.userId)
        || (candidate.email && (member.email ?? member.name).toLowerCase() === candidate.email.toLowerCase())), [people]);

    const words = name.trim().toLowerCase();
    const shown = picked || !words ? [] : candidates
        .filter((candidate) => candidate.name.toLowerCase().includes(words) || candidate.email.toLowerCase().includes(words))
        .slice(0, 5);

    const typed = email.trim();
    const lower = typed.toLowerCase();
    const exact = !picked && typed ? candidates.find((candidate) => candidate.email.toLowerCase() === lower) ?? null : null;
    const who = picked ?? exact;
    const asked = typed ? roster.invites.find((invite) => invite.status === "PENDING" && (invite.email ?? "").toLowerCase() === lower) ?? null : null;
    const emailOk = typed !== "" && inviteProblem(typed) === null;
    const mine = !picked && emailOk && (roster.me.data?.email ?? "").toLowerCase() === lower;

    /* What the button would do, decided once so the button, Enter and the
       line under it agree. */
    const action: "add" | "invite" | null = who
        ? (inside(who) ? null : "add")
        : emailOk && !mine && !asked && !preview && roster.members.isSuccess ? "invite" : null;

    const note = problem
        ?? (mine ? "That’s your own address."
        : who && inside(who) ? `${who.name} is in ${pod.name} already.`
        : who ? `${who.name} is in ${org.name} already, so they’re in straight away.`
        : asked ? (asked.pod_id === pod.id ? `They’ve been invited to ${pod.name} already.` : `They’ve been invited to ${org.name} already. Once they’re in, add them here.`)
        : emailOk && preview ? "In this preview, only people already here can be added."
        : action === "invite" ? `They’re not in ${org.name} yet. They’ll get an email, and join as a Member.`
        : typed && typed.includes("@") && !emailOk ? inviteProblem(typed)
        : null);

    function pick(candidate: Candidate) {
        if (inside(candidate)) return;
        setPicked(candidate);
        setProblem(null);
        /* The fields it replaces are gone, so the next thing to decide is
           what they can do here. */
        requestAnimationFrame(() => roles.current?.querySelector<HTMLInputElement>("input:checked")?.focus());
    }

    function unpick() {
        setPicked(null);
        setName("");
        requestAnimationFrame(() => nameField.current?.focus());
    }

    async function added() {
        await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["pod-detail", pod.id] }),
            queryClient.invalidateQueries({ queryKey: ["pods", org.id] }),
        ]);
    }

    async function go() {
        if (busy || !action) return;
        setBusy(true);
        setProblem(null);
        try {
            if (action === "add" && who) {
                if (preview) {
                    const { addPreviewMember } = await import("@/marketing/preview-source");
                    addPreviewMember(pod.id, who.id, lemmaRoleSaid(role));
                } else {
                    await lemma(pod.id).podMembers.add(pod.id, { organization_member_id: who.id, roles: [role] });
                }
                await added();
                onClose();
                return;
            }
            const created = (await lemma().organizations.invitations.invite(org.id, {
                email: typed,
                /* The least the organization can give; the role here is
                   what they were asked in for. */
                role: "ORG_MEMBER" as never,
                pod_id: pod.id,
                pod_role: role,
                redirect_uri: "/l/" + encodeURIComponent(pod.id),
            })) as OrgInvite;
            rememberName(org.id, typed, name);
            await queryClient.invalidateQueries({ queryKey: ["org-invitations", org.id] });
            const notSent = unsentInvitation(created);
            if (notSent) setUnsent(notSent);
            else onClose();
        } catch (error) {
            setProblem(saidAbout(error, action === "add" ? "Couldn’t add them." : "That invitation was not sent."));
        } finally {
            setBusy(false);
        }
    }

    const title = "Add people to " + pod.name;
    const subtitle = `They’ll see everything in ${pod.name} and can ask it anything.`;
    const close = <button type="button" className="b b--l b--secondary" onClick={onClose}>Close</button>;

    if (!preview && !live) {
        return (
            <Modal title={title} subtitle={subtitle} onClose={onClose} footer={close}>
                <p className="ox-people-dlg__why">This is a sample, so nobody can be added from here.</p>
            </Modal>
        );
    }
    if (!preview && managing.answered && !managing.allowed) {
        return (
            <Modal title={title} subtitle={subtitle} onClose={onClose} footer={close}>
                <p className="ox-people-dlg__why">Only admins of {pod.name} can add people to it. Ask one of them.</p>
            </Modal>
        );
    }
    if (unsent) {
        return (
            <Modal title={"Invited " + unsent.to} subtitle={`They’ll land in ${pod.name} once they accept.`} onClose={onClose} footer={<button type="button" className="b b--l b--primary" onClick={onClose}>Done</button>}>
                <div className="ox-people-dlg">
                    <NotEmailed said={unsent.said} link={unsent.link} to={unsent.to} />
                </div>
            </Modal>
        );
    }

    const lit2 = Math.min(lit, Math.max(0, shown.length - 1));
    const optionId = (index: number) => listId + "-" + index;

    return (
        <Modal
            title={title}
            subtitle={subtitle}
            onClose={onClose}
            footer={(
                <>
                    <button type="button" className="b b--l b--secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" form={formId} className="b b--l b--primary" disabled={!action || busy || (!preview && !managing.answered)}>
                        {busy ? (action === "invite" ? "Sending…" : "Adding…") : action === "invite" ? "Send invite" : "Add"}
                    </button>
                    <p className="ox-people-note" data-bad={problem ? "" : undefined} role={problem ? "alert" : "status"}>{note ?? ""}</p>
                </>
            )}
        >
            <form id={formId} className="ox-people-dlg" onSubmit={(event) => { event.preventDefault(); void go(); }}>
                {picked ? (
                    <div className="ox-people-dlg__pick">
                        <Avatar name={picked.name} size={36} />
                        <span className="ox-people-dlg__pickw">
                            <span className="ox-people-dlg__pickn">{picked.name}</span>
                            <span className="ox-people-dlg__picks">{picked.email && picked.email !== picked.name ? picked.email + " · " : ""}{picked.orgRole} in {org.name}</span>
                        </span>
                        <button type="button" className="b b--s b--quiet b--icon" aria-label="Pick someone else" onClick={unpick}>
                            <Icon name="close" size={16} />
                        </button>
                    </div>
                ) : (
                    <>
                        <div className="kfield">
                            <label htmlFor={listId + "-name"}>Name</label>
                            <input
                                id={listId + "-name"}
                                ref={nameField}
                                className="kinput"
                                placeholder="Start typing a name"
                                autoComplete="off"
                                role="combobox"
                                aria-expanded={shown.length > 0}
                                aria-controls={listId}
                                aria-autocomplete="list"
                                aria-activedescendant={shown.length ? optionId(lit2) : undefined}
                                value={name}
                                onChange={(event) => { setName(event.target.value); setLit(0); setProblem(null); }}
                                onKeyDown={(event) => {
                                    if (!shown.length) return;
                                    if (event.key === "ArrowDown") { event.preventDefault(); setLit((lit2 + 1) % shown.length); }
                                    if (event.key === "ArrowUp") { event.preventDefault(); setLit((lit2 - 1 + shown.length) % shown.length); }
                                    if (event.key === "Enter") { event.preventDefault(); pick(shown[lit2]); }
                                }}
                            />
                            {words && roster.members.isPending && !preview && <small>Reading who’s in {org.name}…</small>}
                            {words && roster.members.isError && <small className="ox-people-dlg__warn">Couldn’t read who’s in {org.name}. Add them by email instead.</small>}
                            {words && roster.members.isSuccess && shown.length === 0 && <small>Nobody in {org.name} by that name. Add them by email.</small>}
                            {shown.length > 0 && (
                                <div className="ox-people-dlg__suggest" id={listId} role="listbox" aria-label={"People in " + org.name}>
                                    {shown.map((candidate, index) => {
                                        const here = inside(candidate);
                                        return (
                                            <div
                                                key={candidate.id}
                                                id={optionId(index)}
                                                role="option"
                                                aria-selected={index === lit2}
                                                aria-disabled={here || undefined}
                                                className="ox-people-dlg__option"
                                                data-lit={index === lit2 ? "" : undefined}
                                                onMouseDown={(event) => event.preventDefault()}
                                                onMouseEnter={() => setLit(index)}
                                                onClick={() => pick(candidate)}
                                            >
                                                <Avatar name={candidate.name} size={30} />
                                                <span className="ox-people-dlg__pickw">
                                                    <span className="ox-people-dlg__pickn">{candidate.name}</span>
                                                    <span className="ox-people-dlg__picks">
                                                        {candidate.email !== candidate.name ? candidate.email : candidate.orgRole}
                                                        {here ? ` · in ${pod.name} already` : ""}
                                                    </span>
                                                </span>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                        <label className="kfield">
                            <span>Email</span>
                            <input
                                className="kinput"
                                type="email"
                                inputMode="email"
                                autoComplete="off"
                                placeholder="name@company.com"
                                value={email}
                                onChange={(event) => { setEmail(event.target.value); setProblem(null); }}
                            />
                        </label>
                    </>
                )}
                <div ref={roles}>
                    <Choices
                        label={"What they can do in " + pod.name}
                        name="lemma-role"
                        value={role}
                        options={LEMMA_ROLES}
                        onChange={setRole}
                    />
                </div>
            </form>
        </Modal>
    );
}

/* ── are you sure ─────────────────────────────────────────────────────── */

/** The last step of taking something away, small: what goes, and two
 *  buttons. Solid red only here, never on the button that opened it. A
 *  refusal stays in the dialog, so the person can read it and try again
 *  or step back. */
export function ConfirmRemove({ title, line, action, busyLabel, onConfirm, onClose }: {
    title: string;
    line: string;
    action: string;
    busyLabel: string;
    onConfirm: () => Promise<void>;
    onClose: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);

    async function confirm() {
        setBusy(true);
        setProblem(null);
        try {
            await onConfirm();
        } catch (error) {
            setProblem(saidAbout(error, "That didn’t go through."));
            setBusy(false);
        }
    }

    return (
        <Modal
            narrow
            title={title}
            subtitle={line}
            onClose={() => { if (!busy) onClose(); }}
            footer={(
                <>
                    <button type="button" className="b b--l b--secondary" disabled={busy} onClick={onClose}>Cancel</button>
                    <button type="button" className="b b--l b--danger" disabled={busy} onClick={() => void confirm()}>{busy ? busyLabel : action}</button>
                </>
            )}
        >
            {problem ? <p className="ox-people-dlg__problem" role="alert">{problem}</p> : null}
        </Modal>
    );
}
