import { ChevronDownIcon, CheckIcon } from "@/ui/icons";
import { useEffect, useRef, useState } from "react";
import type { Org } from "@/data";

export function OrgSwitcher({
    compact = false,
    orgs,
    activeId,
    onPick,
}: {
    compact?: boolean;
    orgs: Org[];
    activeId: string | null;
    onPick: (id: string) => void;
}) {
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        function away(event: MouseEvent) {
            if (!ref.current?.contains(event.target as Node)) setOpen(false);
        }
        function escape(event: KeyboardEvent) {
            if (event.key === "Escape") setOpen(false);
        }
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", escape);
        return () => {
            document.removeEventListener("mousedown", away);
            document.removeEventListener("keydown", escape);
        };
    }, [open]);

    const active = orgs.find((org) => org.id === activeId);

    return (
        <div className="org" ref={ref}>
            <button title={active?.name ?? "Choose organization"} aria-label={"Organization: " + (active?.name ?? "Choose organization")} className="org__button" onClick={() => setOpen((was) => !was)} aria-expanded={open}>
                <span className="org__avatar">{(active?.name ?? "L").slice(0, 1).toUpperCase()}</span>{!compact && <span className="org__name">{active?.name ?? "Choose an organization"}</span>}
                {!compact && <ChevronDownIcon size={14} className="org__chev" />}
            </button>
            {open && (
                <div className="org__menu" role="menu">
                    {orgs.map((org) => (
                        <button
                            key={org.id}
                            className="org__item"
                            role="menuitem"
                            aria-current={org.id === activeId}
                            onClick={() => {
                                onPick(org.id);
                                setOpen(false);
                            }}
                        >
                            <span className="org__name">{org.name}</span>
                            {org.id === activeId && <CheckIcon size={16} className="org__tick" />}
                        </button>
                    ))}
                    {orgs.length === 0 && <span className="org__item">No organizations</span>}
                </div>
            )}
        </div>
    );
}
