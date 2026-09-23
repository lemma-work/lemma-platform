import { PodIndexCardsSkeleton } from '@/components/pod/route-skeletons';

/** Connectors settle into a card grid. */
export default function ConnectorsLoading() {
    return <PodIndexCardsSkeleton tabs={2} cards={6} />;
}
