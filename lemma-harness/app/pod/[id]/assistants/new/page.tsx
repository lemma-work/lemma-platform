import { redirect } from 'next/navigation';

export default async function NewAssistantPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = await params;
    redirect(`/pod/${podId}/agents/new`);
}
