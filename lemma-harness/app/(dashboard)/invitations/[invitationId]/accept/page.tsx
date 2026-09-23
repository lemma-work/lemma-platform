import { InvitationDecision } from "@/components/invitations/invitation-decision";

export default async function InvitationAcceptPage({
    params,
}: {
    params: Promise<{ invitationId: string }>;
}) {
    const { invitationId } = await params;

    return <InvitationDecision invitationId={invitationId} mode="accept" />;
}
