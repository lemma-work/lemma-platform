"use client";

import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { lemma } from "./client";
import { displayName } from "./profile-edit";
import { UserIcon, SettingsIcon } from "@/ui/icons";

/** The account, and the way into settings, which are one thing.
 *
 *  There were two rows here doing the same job: a button saying Settings and
 *  this one saying who you are, both opening the same modal at the same
 *  section. Two doors to one place is one door too many, and the one worth
 *  keeping says whose settings these are.
 *
 *  It takes the opening as a callback because there is one settings modal and
 *  the shell holds it.
 *
 *  Merging the doors cost the word, though. In the rail this is a bare
 *  initials circle, and nobody looking for connectors or billing reads a face
 *  as "settings" — so the face carries a gear in its corner, the way a teammate
 *  face carries its badge, and names itself Settings in the rail's own tip.
 *  Still one door; now it says what it opens.
 */
export function HumanProfile({ compact = false, onOpen }: { compact?: boolean; onOpen: () => void }) {
    const sample = source.label === "sample";
    const user = useQuery({
        queryKey: ["current-user"], queryFn: () => lemma().users.current(),
        enabled: !sample, staleTime: 5 * 60_000, gcTime: 30 * 60_000,
    });
    const name = sample ? "Sample user" : displayName(user.data);
    const email = sample ? "Sample workspace" : user.data?.email;
    const initials = name.split(/\s+/).map(part => part[0]).slice(0, 2).join("").toUpperCase();

    return (
        <button
            className="human-profile"
            onClick={onOpen}
            /* The rail draws its tip from these rather than a native title,
               which is slow, unstyled and would double the tip. */
            data-tip="Settings"
            data-tip-line={name + " · people, connectors, models, billing"}
            title={compact ? undefined : "Settings"}
            aria-label={"Settings — " + name}
            aria-haspopup="dialog"
        >
            <span className="human-avatar">{user.data || sample ? initials : <UserIcon size={24} />}</span>
            <span className="human-profile__text">
                <span>{name}</span>
                <small>{email ?? (user.isPending ? "Loading account…" : "View account")}</small>
            </span>
            <span className="human-profile__chevron human-profile__gear" aria-hidden="true"><SettingsIcon size={17} /></span>
        </button>
    );
}
