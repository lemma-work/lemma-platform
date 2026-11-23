"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import type { Org } from "@/data";
import { ChevronDownIcon } from "@/ui/icons";
import {
    NEW_ORG, SettingsSectionBody, settingsLanding, useSettingsMap,
    type SettingsEntry, type SettingsSection,
} from "./settings-modal";

/** Settings, as a page in the frame.
 *
 *  It was a dialog over whatever you were doing, which suited a quick change
 *  and nothing longer: a model's keys and a server's setup are pages of
 *  reading, and a dialog has no address to come back to. As a page it has
 *  one — `/settings/<section>` — so Back steps between sections and a link
 *  can name one.
 *
 *  The same sections, in the same groups, as the dialog: the list is
 *  `useSettingsMap`, and each section's content `SettingsSectionBody`, so the
 *  two cannot drift. Down the left, plain rows grouped by who owns what (you,
 *  the organization, this computer), then Help; the organization's name a row that
 *  changes it; beside them the chosen section, a heading and one line over
 *  its rows. A form keeps to reading width and a list takes the row. People
 *  is drawn by the frame (`people`), since it reads every Lemma. Connectors
 *  is not here: it is a place of its own in the sidebar. On a phone the list
 *  becomes a row across the top. */
export function SettingsPage({ orgs, activeOrgId, section, focus, onSection, onPickOrg, onNewOrg, people }: {
    orgs: Org[];
    activeOrgId: string | null;
    section: SettingsSection;
    focus: string | null;
    onSection: (section: SettingsSection) => void;
    onPickOrg: (id: string) => void;
    onNewOrg: () => void;
    /** The organization's people, drawn in the People section. */
    people?: ReactNode;
}) {
    const { yours, theirs, machine, help, noun, setupNeeds, org, drawn, entryOf } = useSettingsMap({ orgs, activeOrgId });
    const landing = settingsLanding(section, focus);
    const shown = drawn(landing.section);
    const here = entryOf(shown);
    const headId = useId();
    const nav = useRef<HTMLElement>(null);
    /* On a phone the list is a row across the top that scrolls sideways; a
       section opened by its address -- Help, at the far end -- is scrolled
       into the row so the reader sees which one is lit. */
    useEffect(() => {
        const row = nav.current;
        const lit = row?.querySelector<HTMLElement>("[aria-current=page]");
        if (!row || !lit || row.scrollWidth <= row.clientWidth) return;
        const edge = row.getBoundingClientRect();
        const at = lit.getBoundingClientRect();
        if (at.left < edge.left || at.right > edge.right) row.scrollLeft += at.left - edge.left - 16;
    }, [shown]);

    function item(entry: SettingsEntry) {
        const needs = entry.key === "this-mac-setup" && setupNeeds.length > 0;
        return (
            <button
                key={entry.key}
                type="button"
                className="kset__item"
                aria-current={shown === entry.key ? "page" : undefined}
                onClick={() => onSection(entry.key)}
            >
                {entry.label}
                {needs && <span className="kset__dot" role="img" aria-label="Needs setup" title="Needs setup" />}
            </button>
        );
    }

    return (
        <div className="kpage">
            <header className="ph">
                <div className="ph__row">
                    <div className="ph__words">
                        <h1 className="ph__t">Settings</h1>
                        <p className="ph__line">{org ? "Yours, and " + org.name + "’s for everyone in it." : "Yours, wherever you sign in."}</p>
                    </div>
                </div>
            </header>

            <div className="kset">
                <nav ref={nav} className="kset__nav" aria-label="Settings">
                    <p className="kset__group">You</p>
                    {yours.map(item)}

                    {org && <>
                        {/* Which organization these are the settings of: a
                            row that changes it rather than a caption, since
                            somebody in two came here to change one of them.
                            Its last choice makes another. */}
                        <label className="kset__org">
                            <span className="sr-only">Organization</span>
                            <select
                                value={org.id}
                                onChange={(event) => {
                                    if (event.target.value === NEW_ORG) onNewOrg();
                                    else onPickOrg(event.target.value);
                                }}
                            >
                                {orgs.map((one) => <option key={one.id} value={one.id}>{one.name}</option>)}
                                <option value={NEW_ORG}>New organization…</option>
                            </select>
                            <ChevronDownIcon size={12} />
                        </label>
                        {theirs.map(item)}
                    </>}

                    {machine.length > 0 && <>
                        <p className="kset__group">{noun}</p>
                        {machine.map(item)}
                    </>}

                    {/* Last, and nobody's: the pages that explain Lemma. In
                        the phone's row its one section says it alone. */}
                    <p className="kset__group kset__group--help">Help</p>
                    {help.map(item)}
                </nav>

                <section className={"ksec kset__pane" + (here.wide ? " kset__pane--wide" : "")} aria-labelledby={headId}>
                    {/* People draws its own heading, with Invite beside it. */}
                    {!(shown === "people" && people) && (
                        <header className="ksec__h">
                            <h2 id={headId}>{here.title}</h2>
                            <p>{here.blurb}</p>
                        </header>
                    )}
                    {shown === "people" && people ? people : (
                        <SettingsSectionBody
                            section={shown}
                            org={org}
                            activeOrgId={activeOrgId}
                            focus={shown === landing.section ? landing.focus : null}
                        />
                    )}
                </section>
            </div>
        </div>
    );
}
