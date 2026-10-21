"use client";

import { createContext, useContext, type ReactNode } from "react";
import { PersonIcon } from "@/ui/icons";
import { initials, personName } from "./model";
import type { PersonRef } from "./waiting";

/** How an agent's face is drawn in a chat: given by the chat's page, which
 *  knows the space and its agents. Without it, an agent is its initial. */
export const ChatFaces = createContext<{ agentFace: ((person: PersonRef, size: number) => ReactNode) | null }>({ agentFace: null });

/** Somebody's face in a chat: the teammate's own for the space's agent, an
 *  agent's mark for another, and a person's initials on a round tile --
 *  round is a person, the way the rail keeps it. */
export function Avatar({ person, size = 36 }: { person: PersonRef; size?: number }) {
    const { agentFace } = useContext(ChatFaces);
    if (person.kind === "AGENT") {
        return (
            <span className="cavatar cavatar--agent" style={{ width: size, height: size, fontSize: Math.round(size * 0.4) }}>
                {agentFace ? agentFace(person, size) : (person.name?.trim()[0] ?? "?").toUpperCase()}
            </span>
        );
    }
    /* Somebody nobody has named -- a visitor to the website's chat -- has
       no initials worth drawing: "A contact" would read as "AC". */
    return (
        <span className="cavatar" data-contact={person.kind === "CONTACT" || undefined} style={{ width: size, height: size, fontSize: Math.round(size * 0.36) }}>
            {person.name ? initials(person.name) : person.kind === "CONTACT" ? <PersonIcon size={Math.round(size * 0.5)} aria-hidden="true" /> : initials(personName(person, null))}
        </span>
    );
}
