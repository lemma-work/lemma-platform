import { PodHomeSkeleton } from '@/components/pod/route-skeletons';

/** The pod home is a composer, not an index — no cards. */
export default function PodHomeLoading() {
    return <PodHomeSkeleton />;
}
