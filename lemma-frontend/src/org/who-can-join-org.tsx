"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    source, domainProblem, orgJoinChoices, orgJoinSaid, saidAbout, type OrgJoin, type OrgJoinPolicy,
} from "@/data";
import { CheckIcon, ChevronDownIcon, EditIcon, GlobeIcon, LockIcon, EmailIcon, WarningIcon } from "@/ui/icons";
import { usePicker } from "@/ui/picker";

/** Who may let themselves into the organization.
 *
 *  Under the roster, where a teammate's door is under its own — the same
 *  question at the scale above, in the same place relative to the people it
 *  admits.
 *
 *  It is not the same control, though, and the middle rung is why: a pod opens
 *  to an organization that already exists, while an organization opens to an
 *  email domain, which is a rule that needs a second thing typed before it
 *  means anything. Picking it without one would save a rule that admits
 *  nobody, so picking it asks.
 *
 *  Owner-only, and everybody else reads it: a rule you cannot see is one you
 *  cannot ask an owner to change — and the refusal raised on a teammate's page
 *  ("ask an organization owner to open the organization first") sends people
 *  looking for exactly this.
 */

const MARKS: Record<OrgJoinPolicy, typeof LockIcon> = {
    invited: LockIcon,
    domain: EmailIcon,
    anyone: GlobeIcon,
};

export function WhoCanJoinOrg({ orgId, mayChange }: {
    orgId: string;
    /** `null` while the roster that says what you are here is still arriving.
     *  Drawn as the control, disabled — the alternative is a read-only line
     *  that turns into a control a beat later, which is a screen changing its
     *  mind in front of somebody. */
    mayChange: boolean | null;
}) {
    const queryClient = useQueryClient();
    const { open, setOpen, wrap, menuClass } = usePicker();
    /* Non-null while the domain is being typed. Opening it is the one pick
       that does not write. */
    const [draft, setDraft] = useState<string | null>(null);
    const [wrong, setWrong] = useState<string | null>(null);

    const current = useQuery({
        queryKey: ["org-join", orgId],
        queryFn: () => source.getOrgJoin(orgId),
    });

    const set = useMutation({
        mutationFn: (join: OrgJoin) => source.setOrgJoin(orgId, join),
        onSuccess: (_answer, join) => {
            queryClient.setQueryData(["org-join", orgId], join);
            void queryClient.invalidateQueries({ queryKey: ["org-join", orgId] });
        },
    });

    if (current.isError) {
        return <p className="joins__unreadable">Couldn’t load organization access settings.</p>;
    }

    const join: OrgJoin = (set.isPending && set.variables) || current.data || { policy: "invited", domain: "" };
    const said = orgJoinSaid(join);
    const Mark = MARKS[join.policy];

    function pick(policy: OrgJoinPolicy) {
        setOpen(false);
        setWrong(null);
        /* The domain rule with no domain is not a saveable state, so choosing
           it opens the field instead of writing one that admits nobody. */
        if (policy === "domain" && !join.domain) { setDraft(""); return; }
        set.mutate({ ...join, policy });
    }

    function saveDomain(typed: string) {
        const problem = domainProblem(typed);
        if (problem) { setWrong(problem); return; }
        setWrong(null);
        setDraft(null);
        set.mutate({ policy: "domain", domain: typed.trim().replace(/^@+/, "").toLowerCase() });
    }

    return (
        <div className="joins">
            <p className="joins__ask">Who else can join</p>

            {mayChange === false ? (
                <p className="joins__fixed"><Mark size={15} />{said.who}</p>
            ) : (
                <div className="pick" ref={wrap}>
                    <button
                        className="pick__face"
                        aria-expanded={open}
                        aria-haspopup="menu"
                        aria-label={"Who else can join: " + said.who}
                        disabled={current.isPending || mayChange === null}
                        onClick={() => setOpen((was) => !was)}
                    >
                        <span className="pick__mark"><Mark size={15} /></span>
                        <span className="pick__said">{current.isPending ? "Reading…" : said.who}</span>
                        <ChevronDownIcon size={14} />
                    </button>

                    {open && (
                        <div className={menuClass} role="menu">
                            {orgJoinChoices(join.domain).map((choice) => {
                                const RowMark = MARKS[choice.policy];
                                return (
                                    <button
                                        className="pick__row"
                                        role="menuitem"
                                        key={choice.policy}
                                        onClick={() => pick(choice.policy)}
                                    >
                                        <span className="pick__tick">
                                            {choice.policy === join.policy && <CheckIcon size={13} />}
                                        </span>
                                        <span className="pick__body">
                                            <span className="joins__who"><RowMark size={14} />{choice.who}</span>
                                            <small>{choice.then}</small>
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}

            <p className="joins__then">{current.isPending ? "" : said.then}</p>

            {/* The domain, where the rule in force is about one — and while it
                is being typed, whatever the rule currently is, because that is
                how the rule gets chosen in the first place. */}
            {draft !== null ? (
                <form
                    className="joins__domain"
                    onSubmit={(event) => { event.preventDefault(); saveDomain(draft); }}
                >
                    <span className="joins__at" aria-hidden="true">@</span>
                    <input
                        autoFocus
                        value={draft}
                        aria-label="The domain your addresses end in"
                        placeholder="acme.com"
                        /* Opening it on a domain that is already there means
                           changing that one, so it arrives selected: without
                           this, "Change" hands you a caret at the end of
                           acme.com and the first thing anybody types is a
                           second domain stuck onto the first. */
                        onFocus={(event) => event.currentTarget.select()}
                        onChange={(event) => { setDraft(event.target.value); setWrong(null); }}
                        onKeyDown={(event) => {
                            /* Enter, said out loud. A form with one field
                               submits on Enter by itself in a browser somebody
                               is typing in; it is not something to leave to
                               the implicit rule when the whole point of the
                               field is that it is quick. */
                            if (event.key === "Enter") { event.preventDefault(); saveDomain(event.currentTarget.value); }
                            if (event.key === "Escape") { event.preventDefault(); setDraft(null); setWrong(null); }
                        }}
                    />
                    <button className="btn btn--primary" type="submit" disabled={set.isPending}>
                        {set.isPending ? "Saving…" : "Save"}
                    </button>
                    <button className="btn" type="button" onClick={() => { setDraft(null); setWrong(null); }}>
                        Cancel
                    </button>
                </form>
            ) : join.policy === "domain" && mayChange !== false && (
                <p className="joins__domain">
                    <span>Addresses ending <b>@{join.domain}</b></span>
                    <button
                        className="joins__change"
                        onClick={() => { setWrong(null); setDraft(join.domain); }}
                    ><EditIcon size={13} />Change</button>
                </p>
            )}

            {wrong && <p className="pick__note" role="alert"><WarningIcon size={13} /><span>{wrong}</span></p>}
            {set.isError && (
                <p className="pick__note" role="alert">
                    <WarningIcon size={13} />
                    <span>{saidAbout(set.error, "That could not be saved.")}</span>
                </p>
            )}

            {/* Said here because the refusal it causes is raised somewhere
                else entirely — on a teammate's own page, by somebody who may
                not be able to do anything about it. */}
            <p className="joins__aside">A teammate can only be opened as wide as the organization around it.</p>
        </div>
    );
}
