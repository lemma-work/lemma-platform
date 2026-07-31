import { redirect } from 'next/navigation';

/**
 * Schedules no longer have a section of their own — a trigger is set up on the
 * agent or workflow it wakes up ("Runs when"), and the pod-wide ledger moved
 * under settings. Old links and bookmarks land there.
 */
export default async function LegacySchedulesRedirect({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = await params;
    redirect(`/pod/${podId}/settings/automation`);
}
