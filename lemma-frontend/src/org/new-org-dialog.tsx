"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type NewOrg, type Org } from "@/data";
import { lemma } from "@/session/client";
import { Modal } from "@/shell/modal";
import { isLocalDeployment } from "@/site/config";
import { capitalised, useThisComputer } from "@/desktop/this-computer";
import { canOpenToDomain, domainOf, freshTeamNameFor, isDomainTaken } from "./arrival";

/** A second organization, made from inside the app.
 *
 *  The arrival screen was the only place that could make one, and it only
 *  appears to somebody who belongs to none — so the moment you had one, the
 *  app had no way to make another. It asks the two things that decide it: its
 *  name, and who can join. Somebody who already has a place and asks for
 *  another is setting one up for other people, so there is no "just me" here.
 *
 *  Who can join is choice cards, invite-only first and chosen. Opening it to
 *  a work domain is offered only where it can be had: a company address, a
 *  server others can reach, and a domain no other organization holds. Where
 *  it cannot, the card says why rather than vanishing. A domain is held by
 *  one organization only, and it is usually the person's first one, which the
 *  app only learns when the server refuses; then the card says so and the
 *  choice goes back to invite-only.
 *
 *  `onCreated` runs once the organization list has been read again, so the
 *  caller can switch to the new one and have it found. */
export function NewOrgDialog({ taken, onClose, onCreated }: {
    /** The organizations this person is already in, by name. */
    taken: readonly string[];
    onClose: () => void;
    /** `personal`: they chose a workspace of one. Always false here now:
     *  a workspace of one is made on the way in, not from inside the app. */
    onCreated: (org: Org, personal: boolean) => void;
}) {
    const queryClient = useQueryClient();
    const sample = source.label === "sample";
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current() as Promise<{ email?: string; first_name?: string; last_name?: string } | undefined>,
        enabled: !sample,
        staleTime: 5 * 60_000,
    });
    const email = me.data?.email ?? null;
    const domain = domainOf(email);
    const local = isLocalDeployment();
    const machine = capitalised(useThisComputer());
    const [held, setHeld] = useState(false);
    const [called, setCalled] = useState<string | null>(null);
    const name = called ?? freshTeamNameFor(email, taken);
    const [join, setJoin] = useState<"invited" | "domain">("invited");
    const [problem, setProblem] = useState<string | null>(null);

    /* Why the domain card cannot be picked, or null where it can. */
    const reading = me.isPending && !sample;
    const notDomain = local
        ? "Nobody else can reach " + machine + " until Sharing is on."
        : held
            ? "Another organization already uses @" + domain + ", so people join this one by invitation."
            : sample
                ? "Not in the sample."
                : reading
                    ? "Reading your address…"
                    : me.isError
                        ? "Your address couldn’t be read, so this one starts invite-only."
                        : !canOpenToDomain(email)
                            ? "Only for a work address, and yours is a personal one."
                            : null;

    const make = useMutation({
        mutationFn: async (wanted: NewOrg) => {
            const org = await source.createOrg(wanted);
            await queryClient.invalidateQueries({ queryKey: ["orgs"] });
            return org;
        },
        onSuccess: (org) => onCreated(org, false),
        onError: (failure, wanted) => {
            if (wanted.emailDomain && isDomainTaken(failure)) {
                setHeld(true);
                setJoin("invited");
                setProblem("Another organization already uses @" + wanted.emailDomain + ". Create it invite-only, and invite people by email.");
                return;
            }
            setProblem(failure instanceof Error ? failure.message : "Couldn’t create the organization. Try again.");
        },
    });
    const busy = make.isPending;
    const ready = name.trim().length > 0 && !busy;
    const submit = () => {
        if (!ready) return;
        setProblem(null);
        make.mutate({ name: name.trim(), ...(join === "domain" && !notDomain ? { emailDomain: domain } : {}) });
    };
    /* Closing mid-request would leave it running with nobody to hear how it
       ended. */
    const close = () => { if (!busy) onClose(); };

    return (
        <Modal
            title="New organization"
            subtitle="A separate place, with its own people, Lemmas and plan."
            onClose={close}
            footer={<>
                <button type="button" className="b b--l b--secondary" disabled={busy} onClick={close}>Cancel</button>
                <button type="submit" form="kd-new-org" className="b b--l b--primary" disabled={!ready}>
                    {busy ? "Creating…" : "Create"}
                </button>
            </>}
        >
            <form id="kd-new-org" className="kd-stack" onSubmit={(event) => { event.preventDefault(); submit(); }}>
                <label className="kfield">
                    <span>Name</span>
                    <input
                        className="kinput"
                        value={name}
                        placeholder="Your team’s name"
                        autoComplete="off"
                        disabled={busy}
                        onChange={(event) => setCalled(event.target.value)}
                    />
                </label>
                <div className="kfield" role="radiogroup" aria-label="Who can join">
                    <span>Who can join</span>
                    <div className="kopts">
                        <label className="kopt">
                            <input type="radio" name="kd-new-org-join" value="invited" checked={join === "invited"} disabled={busy} onChange={() => setJoin("invited")} />
                            <span className="kopt__t">Only people you invite</span>
                            <span className="kopt__d">Nobody joins without an invitation.</span>
                            <span className="kopt__dot" aria-hidden="true" />
                        </label>
                        <label className={"kopt" + (notDomain ? " kd-opt--off" : "")}>
                            <input type="radio" name="kd-new-org-join" value="domain" checked={join === "domain" && !notDomain} disabled={busy || Boolean(notDomain)} onChange={() => setJoin("domain")} />
                            <span className="kopt__t">{domain && !local ? "Anyone at " + domain : "Anyone at your company"}</span>
                            <span className="kopt__d">{notDomain ?? "Anyone who signs in with a work address there joins as a Member."}</span>
                            <span className="kopt__dot" aria-hidden="true" />
                        </label>
                    </div>
                </div>
                {problem && <p className="kd-problem" role="alert">{problem}</p>}
            </form>
        </Modal>
    );
}
