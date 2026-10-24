import { useLocalSearchParams } from "expo-router";
import { InvitationScreen } from "../../../../areas/more/welcome/screens";

/** An invitation's link, as the web writes it: `/invitations/{id}/reject`. */
export default function RejectInvitation() {
    const { invitationId } = useLocalSearchParams<{ invitationId: string }>();
    return <InvitationScreen key={String(invitationId ?? "")} id={String(invitationId ?? "")} decision="reject" />;
}
