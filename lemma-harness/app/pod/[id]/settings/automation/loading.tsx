import { PodSettingsLedgerSkeleton } from '@/components/pod/route-skeletons';

/** Triggers settle into a ledger under a count strip, at the settings width. */
export default function TriggersLoading() {
    return <PodSettingsLedgerSkeleton tabs={3} />;
}
