"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type NewOrg, type Org } from "@/data";
import { lemma } from "@/session/client";
import { Modal } from "@/shell/modal";
import { MakeOne } from "./arrival-view";

/** A second organization, made from inside the app.
 *
 *  The arrival screen was the only place that could make one, and it only
 *  appears to somebody who belongs to none — so the moment you had one, the
 *  app had no way to make another. Same form as that screen, because it is the
 *  same decision: just you, or a team. "My team" starts chosen, because
 *  somebody who already has a place and asks for another is usually setting
 *  one up for other people. Invite-only, unlike a first one: a domain belongs
 *  to one organization, and theirs most likely already has it.
 *
 *  `onCreated` runs once the organization list has been read again, so the
 *  caller can switch to the new one and have it found. */
export function NewOrgDialog({ taken, onClose, onCreated }: {
    /** The organizations this person is already in, by name. */
    taken: readonly string[];
    onClose: () => void;
    onCreated: (org: Org) => void;
}) {
    const queryClient = useQueryClient();
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current() as Promise<{ email?: string; first_name?: string; last_name?: string } | undefined>,
        enabled: source.label !== "sample",
        staleTime: 5 * 60_000,
    });
    const [problem, setProblem] = useState<string | null>(null);
    const make = useMutation({
        mutationFn: async (wanted: NewOrg) => {
            const org = await source.createOrg(wanted);
            await queryClient.invalidateQueries({ queryKey: ["orgs"] });
            return org;
        },
        onSuccess: onCreated,
        onError: (failure) => setProblem(failure instanceof Error ? failure.message : "Couldn’t create the organization. Try again."),
    });

    return (
        <Modal title="New organization" subtitle="Its own teammates, people, connectors and billing." narrow onClose={onClose}>
            <div className="new-org">
                {me.isPending && source.label !== "sample" ? (
                    <p className="new-org__quiet" role="status">Loading your account…</p>
                ) : (
                    <MakeOne
                        email={me.data?.email ?? null}
                        name={[me.data?.first_name, me.data?.last_name].filter(Boolean).join(" ") || null}
                        secondary={false}
                        initialKind="team"
                        inviteOnly
                        taken={taken}
                        busy={make.isPending}
                        onMake={(wanted) => { setProblem(null); make.mutate(wanted); }}
                    />
                )}
                {problem && <p className="arrival__problem" role="alert">{problem}</p>}
            </div>
        </Modal>
    );
}
