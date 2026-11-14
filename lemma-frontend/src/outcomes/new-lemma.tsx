"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Org, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { Modal } from "@/shell/modal";
import { characterUrl, CHARACTERS, type CharacterName } from "@/shell/character";
import { TeammateFace } from "@/space/teammate-face";
import { createLemma, freshFace, type LemmaReach } from "./data";
import { Icon } from "./parts";
import { SharedLink } from "./shared-link";

interface OrgPerson {
    /** The organization membership id, which is what adding to a Lemma takes. */
    id: string;
    label: string;
}

interface MemberRow {
    id: string;
    role?: string | null;
    user?: { email?: string | null; name?: string | null } | null;
}

/** The people a new Lemma can be given: everyone in the organization but the
 *  reader, who is in it by making it, and its owners, who see every Lemma
 *  already. */
function useOrgPeople(orgId: string) {
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current() as Promise<{ email?: string } | undefined>,
        enabled: source.label === "live",
        staleTime: 5 * 60_000,
    });
    const members = useQuery({
        queryKey: ["org-members", orgId],
        queryFn: () => lemma().organizations.members.list(orgId, { limit: 100 }),
        enabled: source.label === "live",
        staleTime: 60_000,
    });
    return useMemo<OrgPerson[]>(() => {
        const mine = (me.data?.email ?? "").toLowerCase();
        const rows = (((members.data as { items?: unknown[] } | undefined)?.items) ?? []) as MemberRow[];
        return rows
            .filter((row) => row.role !== "ORG_OWNER")
            .map((row) => ({ id: row.id, label: row.user?.name || row.user?.email || "Member", email: (row.user?.email ?? "").toLowerCase() }))
            .filter((person) => !mine || person.email !== mine)
            .map(({ id, label }) => ({ id, label }));
    }, [me.data, members.data]);
}

/** A new Lemma, the way a chat app makes a channel: a face and a name, what
 *  it is for if you want to say, who can find it, and who is in it. It
 *  starts empty, and its first page says how to fill it: ask it, bring
 *  things in, or add a template. */
export function NewLemmaDialog({ org, pods, onClose, onCreated }: {
    org: Org;
    /** The organization's Lemmas, so a new one wears a face none of them do. */
    pods: readonly Pod[];
    onClose: () => void;
    onCreated: (podId: string, missed: number) => void;
}) {
    const queryClient = useQueryClient();
    const people = useOrgPeople(org.id);
    const [face, setFace] = useState<CharacterName>(() => freshFace(pods.map((one) => one.iconUrl)));
    const [name, setName] = useState("");
    const [purpose, setPurpose] = useState("");
    const [reach, setReach] = useState<LemmaReach>("org");
    const [chosen, setChosen] = useState<readonly OrgPerson[]>([]);
    const [typed, setTyped] = useState("");
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);

    const looking = typed.trim().toLowerCase();
    const offered = people.filter((person) => !chosen.some((one) => one.id === person.id) && (!looking || person.label.toLowerCase().includes(looking)));

    const create = async (event: FormEvent) => {
        event.preventDefault();
        if (!name.trim() || busy) return;
        setBusy(true);
        setProblem(null);
        try {
            const made = await createLemma({ orgId: org.id, name, purpose, face, reach, people: chosen.map((one) => one.id) });
            await queryClient.invalidateQueries({ queryKey: ["pods", org.id] }).catch(() => undefined);
            onCreated(made.podId, made.missed);
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "Couldn’t make it. Try again.");
            setBusy(false);
        }
    };

    if (source.label !== "live") {
        return (
            <Modal title="New Lemma" narrow onClose={onClose}>
                <p className="ox-newl__note">Making a Lemma needs a signed-in session.</p>
            </Modal>
        );
    }

    return (
        <Modal title="New Lemma" narrow onClose={onClose}>
            <form className="ox-newl" onSubmit={(event) => void create(event)}>
                <div className="ox-newl__name">
                    <TeammateFace pod={{ id: face, name: name || "New Lemma", iconUrl: characterUrl(face) }} size={56} live />
                    <label className="ox-newl__field">
                        Name
                        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Support" autoFocus required maxLength={80} />
                    </label>
                </div>
                <fieldset className="ox-newl__faces">
                    <legend>Its face</legend>
                    <div className="ox-newl__cast">
                        {CHARACTERS.map((one) => (
                            <button key={one} type="button" className="ox-newl__pick" aria-pressed={one === face} aria-label={one} title={one} onClick={() => setFace(one)}>
                                <TeammateFace pod={{ id: one, name: one, iconUrl: characterUrl(one, true) }} size={36} />
                            </button>
                        ))}
                    </div>
                </fieldset>
                <label className="ox-newl__field">
                    <span>What it’s for <span className="ox-vs">(optional)</span></span>
                    <input value={purpose} onChange={(event) => setPurpose(event.target.value)} placeholder="Customer questions, from Intercom and email" maxLength={240} />
                </label>
                <fieldset className="ox-newl__reach">
                    <legend>Who can find it</legend>
                    <label className="ox-newl__radio">
                        <input type="radio" name="reach" checked={reach === "org"} onChange={() => setReach("org")} />
                        <span><span>Everyone at {org.name}</span><span className="ox-vs">Anyone here can find it and join.</span></span>
                    </label>
                    <label className="ox-newl__radio">
                        <input type="radio" name="reach" checked={reach === "invited"} onChange={() => setReach("invited")} />
                        <span><span>Only people you add</span><span className="ox-vs">No one else sees it.</span></span>
                    </label>
                </fieldset>
                <div className="ox-newl__field">
                    <span id="newl-people">Add people</span>
                    <div className="ox-newl__people">
                        {chosen.map((person) => (
                            <span key={person.id} className="ox-newl__chip">
                                {person.label}
                                <button type="button" aria-label={`Remove ${person.label}`} onClick={() => setChosen((was) => was.filter((one) => one.id !== person.id))}><Icon name="close" size={12} /></button>
                            </span>
                        ))}
                        <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={chosen.length ? "" : "Name or email"} aria-labelledby="newl-people" />
                    </div>
                    {looking && (
                        <div className="ox-newl__offer" role="listbox" aria-label="People at this organization">
                            {offered.slice(0, 6).map((person) => (
                                <button key={person.id} type="button" role="option" aria-selected={false} onClick={() => { setChosen((was) => [...was, person]); setTyped(""); }}>{person.label}</button>
                            ))}
                            {offered.length === 0 && <p className="ox-vs">Nobody at {org.name} by that name. Add them from People once it’s made.</p>}
                        </div>
                    )}
                </div>
                {problem && <p className="ox-note" role="alert">{problem}</p>}
                <div className="ox-newl__acts">
                    <button type="button" className="ox-btn" onClick={onClose}>Cancel</button>
                    <button type="submit" className="ox-btn ox-btn--primary" disabled={busy || !name.trim()}>{busy ? "Making…" : "Create"}</button>
                </div>
            </form>
            {/* Outside the form above: the link has a form of its own, and
                forms do not nest. */}
            <details className="ox-newl__shared">
                <summary>Or bring in one someone shared</summary>
                <SharedLink />
            </details>
        </Modal>
    );
}
