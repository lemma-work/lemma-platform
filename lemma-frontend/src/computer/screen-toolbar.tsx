"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRightIcon, BackIcon, BrowserIcon, CloseIcon, PeopleIcon, PointerIcon, RefreshIcon, AgentIcon } from "@/ui/icons";
import { PRESETS, type PresetName } from "./presets";
import { useBrowserCommand, type BrowserCommand, type BrowserTab } from "./queries";

/** The bar above the machine's screen: what a browser window would have.
 *
 *  Back, forward, reload and an address, a list of tabs, the screen's size,
 *  and who is driving. Every browser action is one of a closed set the
 *  machine carries out through the same browser the agent drives
 *  (`browser_relay/commands.py`) — there is no "run this" here, and an
 *  address that is not a web address is refused, not opened.
 *
 *  "Agent is driving" is said whenever the agent's commands are arriving, so
 *  a person about to click knows somebody else's click may land first.
 */
export function ScreenToolbar({
    live, driving, mayDrive, agentDriving, watching, preset, sizeNote, onPreset, onTake, onGiveBack,
}: {
    live: boolean;
    driving: boolean;
    mayDrive: boolean;
    agentDriving: boolean;
    /** How many panes are watching, this one included. */
    watching: number;
    preset: PresetName;
    /** Why the last size change did not happen, if it did not. */
    sizeNote: string | null;
    onPreset: (preset: PresetName) => void;
    onTake: () => void;
    onGiveBack: () => void;
}) {
    const command = useBrowserCommand();
    /* Its own mutation, so the five-second refresh never greys out the
       buttons somebody is reaching for. */
    const poll = useBrowserCommand();
    const [tabs, setTabs] = useState<BrowserTab[]>([]);
    const [address, setAddress] = useState("");
    const [editing, setEditing] = useState(false);
    const current = tabs[0];
    const pollTabs = useRef(poll.mutate);
    pollTabs.current = poll.mutate;

    const run = (action: BrowserCommand) => {
        command.mutate(action, { onSuccess: (answer) => setTabs(answer?.tabs ?? []) });
    };

    /* The tabs, and with them the address, kept current while live. Cheap:
       Chrome's own target list, no page touched. */
    useEffect(() => {
        if (!live) return;
        const refresh = () => pollTabs.current({ command: "tabs" }, { onSuccess: (answer) => setTabs(answer?.tabs ?? []) });
        refresh();
        const timer = setInterval(refresh, 5_000);
        return () => clearInterval(timer);
    }, [live]);

    useEffect(() => {
        if (!editing) setAddress(current?.url ?? "");
    }, [current?.url, editing]);

    const disabled = !live || !driving || command.isPending;
    return (
        <div className="screen-bar" role="toolbar" aria-label="Browser">
            <button className="screen-bar__btn" disabled={disabled} title="Back" aria-label="Back" onClick={() => run({ command: "back" })}>
                <BackIcon size={15} />
            </button>
            <button className="screen-bar__btn" disabled={disabled} title="Forward" aria-label="Forward" onClick={() => run({ command: "forward" })}>
                <ArrowRightIcon size={15} />
            </button>
            <button className="screen-bar__btn" disabled={disabled} title="Reload" aria-label="Reload" onClick={() => run({ command: "reload" })}>
                <RefreshIcon size={15} />
            </button>
            <form
                className="screen-bar__address"
                onSubmit={(event) => {
                    event.preventDefault();
                    setEditing(false);
                    if (address.trim()) run({ command: "navigate", url: address.trim() });
                }}
            >
                <input
                    aria-label="Address"
                    value={address}
                    disabled={disabled}
                    spellCheck={false}
                    autoCapitalize="off"
                    inputMode="url"
                    onFocus={() => setEditing(true)}
                    onBlur={() => setEditing(false)}
                    onChange={(event) => setAddress(event.target.value)}
                />
            </form>
            {tabs.length > 1 && (
                <details className="screen-bar__tabs">
                    <summary title="Tabs"><BrowserIcon size={15} /> {tabs.length}</summary>
                    <ul>
                        {tabs.map((tab) => (
                            <li key={tab.id}>
                                <button disabled={disabled} onClick={() => run({ command: "activate", target: tab.id })}>
                                    {tab.title || tab.url}
                                </button>
                                <button disabled={disabled} aria-label={"Close " + (tab.title || tab.url)} onClick={() => run({ command: "close", target: tab.id })}>
                                    <CloseIcon size={12} />
                                </button>
                            </li>
                        ))}
                    </ul>
                </details>
            )}
            <select
                className="screen-bar__size"
                aria-label="Screen size"
                value={preset}
                disabled={!driving}
                title={driving ? "Screen size" : "Take control to change the screen size"}
                onChange={(event) => onPreset(event.target.value as PresetName)}
            >
                {PRESETS.map((option) => (
                    <option key={option.name} value={option.name}>{option.label}</option>
                ))}
            </select>
            {agentDriving && (
                <span className="screen-bar__agent" role="status"><AgentIcon size={14} /> Agent is driving</span>
            )}
            {watching > 1 && (
                <span className="screen-bar__watching" title={`${watching} panes are watching this screen`}>
                    <PeopleIcon size={14} /> {watching}
                </span>
            )}
            {mayDrive && (driving ? (
                <button className="screen-bar__control" onClick={onGiveBack}>Give back</button>
            ) : (
                <button className="screen-bar__control screen-bar__control--take" onClick={onTake}>
                    <PointerIcon size={14} /> Take control
                </button>
            ))}
            {(sizeNote || command.isError) && (
                <p className="screen-bar__note" role="status">
                    {sizeNote ?? commandError(command.error)}
                </p>
            )}
        </div>
    );
}

/** The machine's sentence for a refused command, or a plain one. */
function commandError(error: unknown): string {
    const said = error instanceof Error ? error.message : "";
    if (said && !/^(Request failed|Network request failed)/.test(said)) return capitalise(said) + ".";
    return "The browser didn’t do that. Try again.";
}

function capitalise(text: string): string {
    return text.charAt(0).toUpperCase() + text.slice(1);
}
