"use client";

import { useState } from "react";
import { RefreshIcon } from "@/ui/icons";
import { isForbidden } from "@/session/auth-state";
import { live, useOrgStats, useOrgSummary } from "@/usage/queries";
import { breakdown } from "@/usage/allowance";
import { UsageRanges, UsageRows, UsageTotal } from "@/usage/usage-panel";
import { useFeature } from "@/site/analytics/flags";
import { ContactsCapBlock } from "./contacts-cap";

/** What the whole organization has spent.
 *
 *  A different question from the one on the profile, and not a wider version
 *  of it: that one is yours wherever you work, this one is everybody's here.
 *  The endpoint checks membership and refuses otherwise, so being unable to
 *  read it is an ordinary answer rather than a fault — and it is said that way.
 *
 *  Drawn like your own usage: one big figure for the window, a bar a day,
 *  then rows by model and by kind of work. */
export function OrgUsageSection({ orgId }: { orgId: string }) {
    const [days, setDays] = useState(30);
    const contactsOn = useFeature("contacts");
    const summary = useOrgSummary(orgId, { days });
    const stats = useOrgStats(orgId, { days });

    /* As on your own usage: disabled queries never resolve, so say so
       instead of spinning. */
    if (!live()) {
        return <p className="kset-note">The sample workspace does not spend anything, so there is nothing to report here.</p>;
    }

    if (isForbidden(summary.error)) {
        return (
            <p className="kset-note">
                Only members of this organization can see what it has spent. Your own usage is under You.
            </p>
        );
    }

    return (
        <div className="kset-stack">
            <UsageRanges days={days} onDays={setDays} />
            {summary.isPending && <p className="kset-note" role="status">Adding it up…</p>}
            {summary.isError && (
                <p className="kset-said kset-said--bad" role="alert">
                    Couldn’t load the organization’s usage.
                    <button type="button" className="b b--s b--secondary" onClick={() => void summary.refetch()}><RefreshIcon /> Try again</button>
                </p>
            )}
            {summary.data && <>
                <UsageTotal summary={summary.data} buckets={stats.data?.items ?? []} days={days} />
                <UsageRows title="By model" slices={breakdown(summary.data.total_by_model).slice(0, 8)} />
                <UsageRows title="By kind of work" slices={breakdown(summary.data.total_by_kind).slice(0, 8)} />
            </>}
            {contactsOn && <ContactsCapBlock orgId={orgId} />}
        </div>
    );
}
