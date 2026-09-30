"use client";

import { useEffect, useRef, useState } from "react";
import type { Org, Pod } from "@/data";
import { SPACES } from "@/copy";
import { CheckIcon, ChevronDownIcon, PlusIcon, SettingsIcon } from "@/ui/icons";
import { markTint } from "@/shell/mark";

/** A space's tile: its initial on its own tint. Never a character — the
 *  characters belong to the bots, the ones who do the work, and a place
 *  wearing a face is how a space came to read as a bot. */
export function SpaceTile({ space, size = 24 }: { space: Pick<Pod, "id" | "name" | "iconUrl">; size?: number }) {
    /* Not `iconUrl`: a pod's icon is the character it was hired with, and
       that character now belongs to its assistant. */
    const tint = markTint(space.id);
    return (
        <span className="space-tile" aria-hidden="true" style={{ width: size, height: size, fontSize: Math.round(size * 0.5), ...tint }}>
            {(space.name.trim()[0] ?? "S").toUpperCase()}
        </span>
    );
}

/** The top of the sidebar: which space you are in, and every other place you
 *  could be — the spaces in this organization, and the organizations. One
 *  control for both, because both answer "where am I". */
export function SpaceSwitcher({ compact, space, spaces, orgs, orgId, onSpace, onOrg, onNewSpace, onSettings }: {
    compact: boolean;
    space: Pod | null;
    spaces: Pod[];
    orgs: Org[];
    orgId: string | null;
    onSpace: (id: string) => void;
    onOrg: (id: string) => void;
    onNewSpace: () => void;
    onSettings: () => void;
}) {
    const [open, setOpen] = useState(false);
    const box = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!open) return;
        const away = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
        const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", escape);
        return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", escape); };
    }, [open]);

    const org = orgs.find(each => each.id === orgId);
    const pick = (run: () => void) => () => { run(); setOpen(false); };

    return (
        <div className="sswitch" ref={box}>
            <button className="sswitch__button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(was => !was)}
                title={space ? space.name + (org ? " · " + org.name : "") : "Choose a space"}>
                {space ? <SpaceTile space={space} size={28} /> : <span className="space-tile space-tile--empty" style={{ width: 28, height: 28 }} />}
                {!compact && (
                    <span className="sswitch__text">
                        <span className="sswitch__name">{space?.name ?? "Choose a space"}</span>
                        {org && <small>{org.name}</small>}
                    </span>
                )}
                {!compact && <ChevronDownIcon size={15} className="sswitch__chev" />}
            </button>
            {open && (
                <div className="sswitch__menu" role="menu">
                    <div className="sswitch__label">{SPACES}{org ? " in " + org.name : ""}</div>
                    <div className="sswitch__list">
                        {spaces.map(each => (
                            <button key={each.id} role="menuitem" className="sswitch__item" aria-current={each.id === space?.id} onClick={pick(() => onSpace(each.id))}>
                                <SpaceTile space={each} size={24} />
                                <span>{each.name}</span>
                                {each.id === space?.id && <CheckIcon size={15} className="sswitch__tick" />}
                            </button>
                        ))}
                    </div>
                    {space && (
                        <button role="menuitem" className="sswitch__item" onClick={pick(onSettings)}>
                            <span className="space-tile space-tile--add" style={{ width: 24, height: 24 }}><SettingsIcon size={14} /></span>
                            <span>{space.name} settings</span>
                        </button>
                    )}
                    <button role="menuitem" className="sswitch__item" disabled={!orgId} onClick={pick(onNewSpace)}>
                        <span className="space-tile space-tile--add" style={{ width: 24, height: 24 }}><PlusIcon size={14} /></span>
                        <span>New space</span>
                    </button>
                    {orgs.length > 1 && (
                        <>
                            <div className="sswitch__rule" />
                            <div className="sswitch__label">Organizations</div>
                            {orgs.map(each => (
                                <button key={each.id} role="menuitem" className="sswitch__item" aria-current={each.id === orgId} onClick={pick(() => onOrg(each.id))}>
                                    <span className="org__avatar">{each.name.slice(0, 1).toUpperCase()}</span>
                                    <span>{each.name}</span>
                                    {each.id === orgId && <CheckIcon size={15} className="sswitch__tick" />}
                                </button>
                            ))}
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
