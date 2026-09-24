"use client";

import { useState, type ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Org } from "@/data";
import { source } from "@/data";
import { hasToken, lemma } from "@/session/client";
import { useSession } from "@/session/session";
import { displayName } from "@/session/profile-edit";
import { ProfilePanel } from "@/session/profile-panel";
import { AppearancePanel } from "@/session/theme";
import { UsagePanel } from "@/usage/usage-panel";
import { PlanSection } from "@/billing/plan-section";
import { TeamBillingSection } from "@/billing/team-billing";
import { PeopleSection } from "@/org/people";
import { ConnectorsSection } from "@/org/connectors";
import { ModelsSection } from "@/org/models";
import { OrgUsageSection } from "@/org/org-usage";
import { Modal } from "@/shell/modal";
import {
    RefreshIcon, SignOutIcon, EmailIcon, ProfileIcon, AppearanceIcon,
    UsageIcon, PeopleIcon, ConnectorIcon, KeyIcon, OrgIcon, CardIcon, ReceiptIcon,
} from "@/ui/icons";

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
 */
export type SettingsSection =
    | "account" | "appearance" | "usage" | "plan"
    | "people" | "connectors" | "models" | "org-usage" | "team-billing";

interface Entry {
    key: SettingsSection;
    label: string;
    icon: ComponentType<{ size?: number }>;
    title: string;
    blurb: string;
}

const YOURS: Entry[] = [
    { key: "account", label: "Account", icon: ProfileIcon, title: "Account", blurb: "The person behind the work." },
    { key: "appearance", label: "Appearance", icon: AppearanceIcon, title: "Appearance", blurb: "How this app looks on this screen." },
    { key: "usage", label: "Usage", icon: UsageIcon, title: "Your usage", blurb: "What you have spent, and what you may spend." },
    { key: "plan", label: "Plan", icon: CardIcon, title: "Your plan",
        blurb: "What you pay for, and what it would take to be allowed to spend more." },
];

const THEIRS: Entry[] = [
    { key: "people", label: "People", icon: PeopleIcon, title: "People", blurb: "Who is in this organization." },
    { key: "connectors", label: "Connectors", icon: ConnectorIcon, title: "Connectors",
        blurb: "Accounts this organization has authorised, and what a teammate may reach with them." },
    { key: "models", label: "Models", icon: KeyIcon, title: "Models",
        blurb: "Choose models and coding agents for your teammates. Provider keys are shared with the organization. Coding agents use the access configured on their paired computer." },
    { key: "org-usage", label: "Usage", icon: UsageIcon, title: "Organization usage", blurb: "Usage across this organization." },
    { key: "team-billing", label: "Billing", icon: ReceiptIcon, title: "Billing",
        blurb: "What this organization pays for, how many seats it has bought, and who may change that." },
];

export function SettingsModal({
    orgs,
    activeOrgId,
    onPickOrg,
    initial = "account",
    onClose,
}: {
    orgs: Org[];
    activeOrgId: string | null;
    onPickOrg: (id: string) => void;
    initial?: SettingsSection;
    onClose: () => void;
}) {
    const [section, setSection] = useState<SettingsSection>(initial);
    const session = useSession();
    const [leaving, setLeaving] = useState(false);
    const [signOutError, setSignOutError] = useState<string | null>(null);
    const sample = source.label === "sample";
    const org = orgs.find((candidate) => candidate.id === activeOrgId) ?? null;

    const user = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: !sample,
        staleTime: 5 * 60_000,
        gcTime: 30 * 60_000,
    });

    const name = sample ? "Sample user" : displayName(user.data);
    const here = [...YOURS, ...THEIRS].find((entry) => entry.key === section) ?? YOURS[0];

    function nav(entry: Entry) {
        return (
            <button key={entry.key} aria-current={section === entry.key} onClick={() => setSection(entry.key)}>
                <entry.icon size={17} />
                {entry.label}
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
                                {YOURS.map((entry) => <option key={entry.key} value={entry.key}>{entry.label}</option>)}
                            </optgroup>
                            {org && (
                                <optgroup label={org.name}>
                                    {THEIRS.map((entry) => <option key={entry.key} value={entry.key}>{entry.label}</option>)}
                                </optgroup>
                            )}
                        </select>
                    </label>
                    {/* Only where there is a choice to make. */}
                    {org && orgs.length > 1 && (
                        <label className="settings-picker__field">
                            <span className="sr-only">Organization</span>
                            <select value={org.id} onChange={(event) => onPickOrg(event.target.value)}>
                                {orgs.map((candidate) => (
                                    <option key={candidate.id} value={candidate.id}>{candidate.name}</option>
                                ))}
                            </select>
                        </label>
                    )}
                </div>

                <nav className="settings-nav" aria-label="Settings">
                    <span className="settings-nav__label">You</span>
                    {YOURS.map(nav)}

                    {org && <>
                        {/* Which organization these are the settings of — a
                            control rather than a caption, because somebody in
                            two of them came here to change one of them and
                            should not have to leave to say which. */}
                        <label className="settings-org">
                            <OrgIcon size={15} />
                            <select
                                value={org.id}
                                onChange={(event) => onPickOrg(event.target.value)}
                                aria-label="Organization"
                                disabled={orgs.length < 2}
                            >
                                {orgs.map((candidate) => (
                                    <option key={candidate.id} value={candidate.id}>{candidate.name}</option>
                                ))}
                            </select>
                        </label>
                        {THEIRS.map(nav)}
                    </>}
                </nav>

                <div className="settings-pane">
                    <div className="settings-pane__inner">
                        <div className="settings-pane__head">
                            <h3>{here.title}</h3>
                            <p>{here.blurb}</p>
                        </div>

                        {section === "account" && (
                            <div className="settings-account">
                                {sample && <span className="account-tag">Sample account</span>}
                                {user.isError && (
                                    <div className="account-error">
                                        <p>Your account could not be loaded.</p>
                                        <button className="btn" onClick={() => void user.refetch()}><RefreshIcon size={16} /> Try again</button>
                                    </div>
                                )}
                                {!sample && user.isPending && <p role="status">Loading your profile…</p>}
                                {user.data && <>
                                    <div className="settings-who">
                                        <span className="human-avatar human-avatar--large">
                                            {name.split(/\s+/).map((part) => part[0]).slice(0, 2).join("").toUpperCase()}
                                        </span>
                                        <div>
                                            <strong>{name}</strong>
                                            <small><EmailIcon size={14} />{user.data.email}</small>
                                        </div>
                                    </div>
                                    <ProfilePanel user={user.data} />
                                </>}
                                {!sample && (
                                    <div className="settings-leave">
                                        <button
                                            className="account-disconnect"
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
                                        ><SignOutIcon size={17} />{leaving ? "Signing out…" : "Sign out"}</button>
                                        {signOutError && <p role="alert">{signOutError}</p>}
                                        {hasToken() && <a className="account-aside" href="/connect">This browser is using a bearer token</a>}
                                    </div>
                                )}
                            </div>
                        )}

                        {section === "appearance" && <AppearancePanel />}
                        {section === "usage" && <UsagePanel orgId={activeOrgId} />}
                        {section === "plan" && <PlanSection />}
                        {org && section === "people" && <PeopleSection orgId={org.id} />}
                        {org && section === "connectors" && <ConnectorsSection orgId={org.id} />}
                        {org && section === "models" && <ModelsSection orgId={org.id} />}
                        {org && section === "org-usage" && <OrgUsageSection orgId={org.id} />}
                        {org && section === "team-billing" && <TeamBillingSection orgId={org.id} />}
                    </div>
                </div>
            </div>
        </Modal>
    );
}
