"use client";

import { useMemo, useState } from "react";
import type { Connector } from "@/data";
import { Modal } from "@/shell/modal";
import { SearchIcon } from "@/ui/icons";

/** Choosing a service from the catalogue, by name.
 *
 *  The Connectors page's "Connect an account" and "an OAuth app of your own"
 *  ask the same question first — which service? — of a catalogue of a couple
 *  of hundred, so both ask it here: a search over the names, and the whole
 *  row the target. One that cannot be picked here says why in its row,
 *  rather than being left out or offering a pick that can only fail. */
export function ServicePicker({ title, subtitle, lead, empty = "Nothing to choose from here yet.", services, why, onPick, onClose }: {
    title: string;
    subtitle?: string;
    lead?: string;
    /** What to say when there is nothing to choose. */
    empty?: string;
    services: Connector[];
    /** Why one cannot be picked by this person, or null when it can. */
    why?: (service: Connector) => string | null;
    onPick: (service: Connector) => void;
    onClose: () => void;
}) {
    const [query, setQuery] = useState("");
    const term = query.trim().toLowerCase();
    const shown = useMemo(
        () => term ? services.filter((one) => one.title.toLowerCase().includes(term) || one.id.toLowerCase().includes(term)) : services,
        [services, term],
    );

    return (
        <Modal title={title} subtitle={subtitle} narrow onClose={onClose}>
            <div className="connect-form">
                {services.length === 0 ? (
                    <p role="status" className="connect-lead">{empty}</p>
                ) : (
                    <>
                        {lead && <p className="connect-lead">{lead}</p>}
                        {services.length > 6 && (
                            <label className="ksearch kconn-find">
                                <SearchIcon size={15} />
                                <input value={query} autoFocus aria-label="Find a service" placeholder={"Search " + services.length + " services"}
                                    onChange={(event) => setQuery(event.target.value)} />
                            </label>
                        )}
                        <div className="connect-picks">
                            {shown.map((service) => {
                                const blocked = why?.(service) ?? null;
                                return (
                                    <button type="button" className="connect-pick" key={service.id} disabled={Boolean(blocked)} onClick={() => onPick(service)}>
                                        <span className="connect-pick__glyph">
                                            {service.icon
                                                ? <img src={service.icon} alt="" aria-hidden="true" width={20} height={20} />
                                                : service.title.slice(0, 1)}
                                        </span>
                                        <span>
                                            <strong>{service.title}</strong>
                                            {(blocked ?? service.description) && <small>{blocked ?? service.description}</small>}
                                        </span>
                                    </button>
                                );
                            })}
                            {shown.length === 0 && <p className="connect-lead">Nothing matches “{query}”.</p>}
                        </div>
                    </>
                )}
            </div>
        </Modal>
    );
}
