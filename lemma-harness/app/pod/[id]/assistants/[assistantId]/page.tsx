import { redirect } from 'next/navigation';

export default async function AssistantPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = await params;
    redirect(`/pod/${podId}/ai`);
}
