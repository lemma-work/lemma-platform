"use client";

import { useState, type ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Org } from "@/data";
import { source } from "@/data";
import { hasToken, lemma } from "@/session/client";
import { useSession } from "@/session/session";
import { displayName } from "@/session/profile-edit";
import { PROFILE_FIRST_FIELD, ProfilePanel } from "@/session/profile-panel";
import { LinkTelegramEntry } from "@/session/link-telegram";
import { DeleteAccountEntry } from "@/session/delete-account";
import { AppearancePanel } from "@/session/theme";
import { UsagePanel } from "@/usage/usage-panel";
import { PlanSection } from "@/billing/plan-section";
import { TeamBillingSection } from "@/billing/team-billing";
import { PeopleSection } from "@/org/people";
import { ModelsSection } from "@/org/models";
import { OrgUsageSection } from "@/org/org-usage";
import { Modal } from "@/shell/modal";
import { sellsHere } from "@/site/config";
import {
    RefreshIcon, SignOutIcon, ProfileIcon, AppearanceIcon,
    UsageIcon, PeopleIcon, KeyIcon, OrgIcon, CardIcon, ReceiptIcon,
    ComputerIcon, TerminalIcon, GlobeIcon, UpgradeIcon, SettingsIcon,
    QuestionIcon, ExternalIcon,
} from "@/ui/icons";
import { ThisMacPane, isThisMacSection, useThisMacAvailability, type ThisMacSection } from "@/desktop/this-mac-settings";
import { useServerSetupNeeds } from "@/desktop/this-mac-setup";
import { capitalised, useThisComputer } from "@/desktop/this-computer";

/** One door for everything that is not a teammate.
 *
 *  There were four — a gear that opened the platform in a new tab, an
 *  appearance menu beside it, a full-screen organization screen, and a profile
 *  dialog — and between them "where do I change this" had no answer worth
 *  giving.
 *
 *  Grouped by what owns the setting, which is the only division that survives
 *  contact with the platform: some of these follow you wherever you work, and
 *  the rest belong to the organization and are paid for once.
 *
 *  Connectors is no longer one of them: it is a place of its own in the
 *  sidebar. "connectors" stays a section name only so old links that ask
 *  for it still type-check on their way there (the frame sends them on).
 *
 *  Help is last, owned by nobody: where the documentation and what changed
 *  lately are, which used to be a "?" menu beside the account.
 */
export type SettingsSection =
    | "account" | "appearance" | "usage" | "plan"
    | "people" | "connectors" | "models" | "org-usage" | "team-billing"
    | ThisMacSection
    | "help";

export interface SettingsEntry {
    key: SettingsSection;
    label: string;
    icon: ComponentType<{ size?: number }>;
    title: string;
    /** One plain line under the title. */
    blurb: string;
    /** A list that takes the full width beside the menu, rather than a form
     *  kept to reading width. */
    wide?: boolean;
}

const YOURS: SettingsEntry[] = [
    { key: "account", label: "Account", icon: ProfileIcon, title: "Account", blurb: "Who you are here." },
    { key: "appearance", label: "Appearance", icon: AppearanceIcon, title: "Appearance", blurb: "How Lemma looks on this device." },
    { key: "usage", label: "Usage", icon: UsageIcon, title: "Your usage", blurb: "What your work has used, and what you may use.", wide: true },
    { key: "plan", label: "Plan", icon: CardIcon, title: "Your plan", blurb: "What you pay for, and what each plan gives you.", wide: true },
];

const THEIRS: SettingsEntry[] = [
    { key: "people", label: "People", icon: PeopleIcon, title: "People",
        blurb: "Everyone here, what they can do, and the Lemmas each one is in.", wide: true },
    { key: "models", label: "Models", icon: KeyIcon, title: "Models",
        blurb: "What your Lemmas think with. Keys you add are shared with everyone here.", wide: true },
    { key: "org-usage", label: "Usage", icon: UsageIcon, title: "Usage",
        blurb: "What this organization has used, by model and by kind of work.", wide: true },
    { key: "team-billing", label: "Billing", icon: ReceiptIcon, title: "Billing",
        blurb: "What this organization pays for, and what each plan gives everyone in it.", wide: true },
];

const HELP: SettingsEntry[] = [
    { key: "help", label: "Help", icon: QuestionIcon, title: "Help", blurb: "How Lemma works, and what changed lately." },
];

/** This computer: a third owner, after you and the organization. Only in the
 *  desktop app, on a local install, for the person whose computer it is —
 *  `useThisMacAvailability` has the rule. Named with the machine's own noun,
 *  so a Windows install says "This PC". */
function thisMacEntries(noun: string): SettingsEntry[] {
    return [
        { key: "this-mac", label: "Overview", icon: ComputerIcon, title: noun,
            blurb: "Whether Lemma is running here, and how it starts." },
        { key: "this-mac-setup", label: "Server setup", icon: SettingsIcon, title: "Server setup",
            blurb: `What the Lemma server on ${noun.toLowerCase()} can do, and the keys it needs to do it.` },
        { key: "this-mac-agents", label: "Coding agents", icon: TerminalIcon, title: "Coding agents",
            blurb: `What runs on ${noun.toLowerCase()}.` },
        { key: "this-mac-sharing", label: "Sharing", icon: GlobeIcon, title: "Sharing",
            blurb: "Who can reach this Lemma, and who can make an account on it." },
        { key: "this-mac-updates", label: "Updates", icon: UpgradeIcon, title: "Updates",
            blurb: "Which Lemma this is, and whether there is a newer one." },
    ];
}

/** The organization picker's last choice, which makes one instead of
 *  choosing one. Not a uuid, so it can never be an organization's id. */
export const NEW_ORG = "new-organization";

/** Sections about paying, which a local installation has nothing to say in:
 *  it runs the open-source backend on somebody's own machine, with no plans
 *  to pick and no seats to buy. Hidden rather than shown empty, because an
 *  empty Plan page reads as "billing is broken", not "there is no billing".
 *  The phone app's WebViews leave them out too (`sellsHere`). */
const BILLING: ReadonlySet<SettingsSection> = new Set(["plan", "team-billing"]);

function offered(entries: SettingsEntry[]): SettingsEntry[] {
    return sellsHere() ? entries : entries.filter((entry) => !BILLING.has(entry.key));
}

/** Where a request for a section lands. Advanced became the last part of
 *  Server setup; a link to it opens Server setup there, or at the form it
 *  named. */
export function settingsLanding(wanted: SettingsSection, focus: string | null): { section: SettingsSection; focus: string | null } {
    return wanted === "this-mac-advanced" ? { section: "this-mac-setup", focus: focus ?? "advanced" } : { section: wanted, focus };
}

/** The sections, grouped by owner, for whoever draws a way between them —
 *  the dialog and the page alike, so the two can never offer different
 *  lists. `drawn` says which section a request actually shows: one this
 *  person has, or Account. */
export function useSettingsMap({ orgs, activeOrgId }: { orgs: Org[]; activeOrgId: string | null }) {
    const yours = offered(YOURS);
    const theirs = offered(THEIRS);
    const noun = capitalised(useThisComputer());
    const availability = useThisMacAvailability();
    const machine = availability === "hidden" ? [] : thisMacEntries(noun);
    const setupNeeds = useServerSetupNeeds();
    const org = orgs.find((candidate) => candidate.id === activeOrgId) ?? null;
    const help = HELP;
    const every = [...yours, ...theirs, ...thisMacEntries(noun), ...help];
    /* A This Mac section is kept while the installation is still answering
       whether you own it, so the menu's ⌘, lands where it asked. */
    const drawn = (wanted: SettingsSection): SettingsSection => {
        if (yours.some((entry) => entry.key === wanted)) return wanted;
        if (org && theirs.some((entry) => entry.key === wanted)) return wanted;
        if (isThisMacSection(wanted) && availability !== "hidden") return wanted;
        if (help.some((entry) => entry.key === wanted)) return wanted;
        return "account";
    };
    const entryOf = (key: SettingsSection): SettingsEntry => every.find((entry) => entry.key === key) ?? yours[0];
    return { yours, theirs, machine, help, noun, setupNeeds, org, drawn, entryOf };
}

/** One section's content, without the frame around it: the dialog draws it
 *  in its pane and the page under its heading. */
export function SettingsSectionBody({ section, org, activeOrgId, focus }: {
    section: SettingsSection;
    org: Org | null;
    activeOrgId: string | null;
    /** A part of the section to open at — Server setup's Google form, say. */
    focus: string | null;
}) {
    return (
        <>
            {section === "account" && <AccountSection />}
            {section === "appearance" && <AppearancePanel />}
            {section === "usage" && <UsagePanel orgId={activeOrgId} />}
            {section === "plan" && <PlanSection />}
            {org && section === "people" && <PeopleSection orgId={org.id} />}
            {org && section === "models" && <ModelsSection orgId={org.id} />}
            {org && section === "org-usage" && <OrgUsageSection orgId={org.id} />}
            {org && section === "team-billing" && <TeamBillingSection orgId={org.id} orgName={org.name} />}
            {isThisMacSection(section) && <ThisMacPane section={section} focus={focus} />}
            {section === "help" && <HelpSection />}
        </>
    );
}

/** The pages that explain Lemma, each opened in a tab of its own so the
 *  reader keeps their place here. Only pages that exist. */
const HELP_PAGES: readonly { href: string; title: string; line: string }[] = [
    { href: "/docs", title: "Documentation", line: "How Lemmas, apps and automations work" },
    { href: "/changelog", title: "What’s new", line: "The latest changes, newest first" },
    { href: "/contact", title: "Contact us", line: "Ask the team a question" },
];

function HelpSection() {
    return (
        <div className="klist">
            {HELP_PAGES.map((page) => (
                <a key={page.href} className="krow krow--open kset-out" href={page.href} target="_blank" rel="noreferrer">
                    <span className="krow__v">
                        <b>{page.title}</b>
                        <span>{page.line}</span>
                    </span>
                    <span className="krow__end">
                        <ExternalIcon size={16} aria-hidden="true" />
                        <span className="sr-only">(opens in a new tab)</span>
                    </span>
                </a>
            ))}
        </div>
    );
}

/** Account: who is signed in, the ways they are known, their details, and
 *  the two ways out.
 *
 *  Rows first, since most people come to read one of them (which address,
 *  whether Telegram knows them); the details after, as a form at reading
 *  width; and deleting the account last, apart from signing out so neither
 *  is mistaken for the other. */
function AccountSection() {
    const session = useSession();
    const [leaving, setLeaving] = useState(false);
    const [signOutError, setSignOutError] = useState<string | null>(null);
    const sample = source.label === "sample";
    const user = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: !sample,
        staleTime: 5 * 60_000,
        gcTime: 30 * 60_000,
    });
    const name = sample ? "Sample user" : displayName(user.data);

    return (
        <div className="kset-stack">
            {sample && <p className="kset-note"><span className="ktag">Example</span> This is the sample account, so there is nothing of yours to change.</p>}
            {user.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    Your account couldn’t be loaded.
                    <button type="button" className="b b--s b--secondary" onClick={() => void user.refetch()}>
                        <RefreshIcon /> Try again
                    </button>
                </p>
            )}
            {!sample && user.isPending && <p className="kset-note" role="status">Loading your account…</p>}
            {(sample || user.data || !user.isPending) && (
                <div className="klist">
                    {user.data && <>
                        <div className="krow krow--kv">
                            <span className="krow__k">Name</span>
                            <span className="krow__v"><b>{name}</b></span>
                            <span className="krow__end">
                                {/* The form below is where it changes; this
                                    takes the reader to it. */}
                                <button
                                    type="button"
                                    className="b b--s b--secondary"
                                    onClick={() => document.getElementById(PROFILE_FIRST_FIELD)?.focus()}
                                >Change</button>
                            </span>
                        </div>
                        <div className="krow krow--kv">
                            <span className="krow__k">Email</span>
                            <span className="krow__v"><b>{user.data.email}</b><span>How you sign in</span></span>
                            <span className="krow__end" />
                        </div>
                    </>}
                    {/* Beside the profile, not in its form: it proves an
                        account rather than saving a field. The sample has no
                        profile to edit but does have places to be reached,
                        so it links there too. */}
                    {(sample || user.data) && <LinkTelegramEntry />}
                    {!sample && (
                        <div className="krow krow--kv">
                            <span className="krow__k">This browser</span>
                            <span className="krow__v kset-wrap">
                                <b>Signed in</b>
                                {signOutError
                                    ? <span className="kset-said--bad" role="alert">{signOutError}</span>
                                    : hasToken() && <span><a href="/connect">Using a bearer token</a></span>}
                            </span>
                            <span className="krow__end">
                                <button
                                    type="button"
                                    className="b b--s b--secondary"
                                    disabled={leaving}
                                    onClick={() => {
                                        /* No confirmation. Signing out is not destructive and is
                                           one click to undo; a dialog in front of it is a dialog
                                           people learn to click through. */
                                        setLeaving(true);
                                        setSignOutError(null);
                                        void session.signOut().catch(() => {
                                            setLeaving(false);
                                            setSignOutError("We couldn’t confirm sign-out. Check your connection and try again.");
                                        });
                                    }}
                                ><SignOutIcon />{leaving ? "Signing out…" : "Sign out"}</button>
                            </span>
                        </div>
                    )}
                </div>
            )}
            {user.data && <>
                <p className="ksub">Your details <span>Times are shown to you in your time zone</span></p>
                <ProfilePanel user={user.data} />
            </>}
            {!sample && user.data && <DeleteAccountEntry email={user.data.email} />}
        </div>
    );
}

export function SettingsModal({
    orgs,
    activeOrgId,
    onPickOrg,
    onNewOrg = null,
    initial = "account",
    initialFocus = null,
    onClose,
}: {
    orgs: Org[];
    activeOrgId: string | null;
    onPickOrg: (id: string) => void;
    /** Make another organization. Offered from the organization picker,
     *  which is where somebody choosing between them already is. */
    onNewOrg?: (() => void) | null;
    initial?: SettingsSection;
    /** A part of the section to open at — Advanced's Google form, say. */
    initialFocus?: string | null;
    onClose: () => void;
}) {
    const { yours, theirs, machine, help, noun, setupNeeds, org, drawn, entryOf } = useSettingsMap({ orgs, activeOrgId });
    const opening = settingsLanding(initial, initialFocus);
    const [requested, setSection] = useState<SettingsSection>(opening.section);
    const section = drawn(requested);
    /* The organization picker doubles as the way to make another: one more
       choice at the foot of the list, never selected itself. */
    const pickOrg = (value: string) => {
        if (value === NEW_ORG) onNewOrg?.();
        else onPickOrg(value);
    };
    const orgOptions = <>
        {orgs.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>{candidate.name}</option>
        ))}
        {onNewOrg && <option value={NEW_ORG}>New organization…</option>}
    </>;
    const here = entryOf(section);

    function nav(entry: SettingsEntry) {
        const needs = entry.key === "this-mac-setup" && setupNeeds.length > 0;
        return (
            <button key={entry.key} aria-current={section === entry.key} onClick={() => setSection(entry.key)}>
                <entry.icon size={17} />
                {entry.label}
                {needs && <span className="settings-nav__dot" role="img" aria-label="Needs setup" title="Needs setup" />}
            </button>
        );
    }

    return (
        <Modal title="Settings" wide flush onClose={onClose}>
            <div className="settings-layout">
                {/* Narrow gets a picker rather than the list.
                    Laid out as a strip, the list scrolled sideways with its
                    sections off the edge, and the group headings had to be
                    hidden to fit — so the organization's name sat in the row
                    of tabs looking like one, and nothing said which sections
                    belonged to it. A select keeps the grouping and fits. */}
                <div className="settings-picker">
                    <label className="settings-picker__field">
                        <span className="sr-only">Section</span>
                        <select
                            value={section}
                            onChange={(event) => setSection(event.target.value as SettingsSection)}
                        >
                            <optgroup label="You">
                                {yours.map((entry) => <option key={entry.key} value={entry.key}>{entry.label}</option>)}
                            </optgroup>
                            {org && (
                                <optgroup label={org.name}>
                                    {theirs.map((entry) => <option key={entry.key} value={entry.key}>{entry.label}</option>)}
                                </optgroup>
                            )}
                            {machine.length > 0 && (
                                <optgroup label={noun}>
                                    {machine.map((entry) => (
                                        <option key={entry.key} value={entry.key}>
                                            {entry.label}{entry.key === "this-mac-setup" && setupNeeds.length ? " · needs setup" : ""}
                                        </option>
                                    ))}
                                </optgroup>
                            )}
                            <optgroup label="Help">
                                {help.map((entry) => <option key={entry.key} value={entry.key}>{entry.label}</option>)}
                            </optgroup>
                        </select>
                    </label>
                    {/* Only where there is a choice to make — another
                        organization, or a new one. */}
                    {org && (orgs.length > 1 || onNewOrg) && (
                        <label className="settings-picker__field">
                            <span className="sr-only">Organization</span>
                            <select value={org.id} onChange={(event) => pickOrg(event.target.value)}>
                                {orgOptions}
                            </select>
                        </label>
                    )}
                </div>

                <nav className="settings-nav" aria-label="Settings">
                    <span className="settings-nav__label">You</span>
                    {yours.map(nav)}

                    {org && <>
                        {/* Which organization these are the settings of — a
                            control rather than a caption, because somebody in
                            two of them came here to change one of them and
                            should not have to leave to say which. */}
                        <label className="settings-org">
                            <OrgIcon size={15} />
                            <select
                                value={org.id}
                                onChange={(event) => pickOrg(event.target.value)}
                                aria-label="Organization"
                                disabled={orgs.length < 2 && !onNewOrg}
                            >
                                {orgOptions}
                            </select>
                        </label>
                        {theirs.map(nav)}
                    </>}

                    {machine.length > 0 && <>
                        <span className="settings-nav__label">{noun}</span>
                        {machine.map(nav)}
                    </>}

                    <span className="settings-nav__label">Help</span>
                    {help.map(nav)}
                </nav>

                <div className="settings-pane">
                    <div className="settings-pane__inner">
                        <div className="settings-pane__head">
                            <h3>{here.title}</h3>
                            <p>{here.blurb}</p>
                        </div>
                        <SettingsSectionBody
                            section={section}
                            org={org}
                            activeOrgId={activeOrgId}
                            focus={section === opening.section ? opening.focus : null}
                        />
                    </div>
                </div>
            </div>
        </Modal>
    );
}
