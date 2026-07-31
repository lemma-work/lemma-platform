import { redirect } from 'next/navigation';

/**
 * Surfaces no longer have a page of their own — they are configured from the
 * agent that answers on them. Old links (and the retired nav item) land here.
 */
export default async function LegacySurfacesRedirect({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = await params;
    redirect(`/pod/${podId}/ai`);
}
