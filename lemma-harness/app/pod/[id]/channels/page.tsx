import { redirect } from 'next/navigation';

/** Long-retired name for surfaces, which now live inside agents. */
export default async function LegacyChannelsRedirect({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = await params;
    redirect(`/pod/${podId}/ai`);
}
