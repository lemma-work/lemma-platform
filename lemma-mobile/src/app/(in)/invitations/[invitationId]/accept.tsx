import { useLocalSearchParams } from "expo-router";
import { InvitationScreen } from "../../../../areas/more/welcome/screens";

/** An invitation's link, as the web writes it: `/invitations/{id}/accept`. */
export default function AcceptInvitation() {
    const { invitationId } = useLocalSearchParams<{ invitationId: string }>();
    return <InvitationScreen key={String(invitationId ?? "")} id={String(invitationId ?? "")} decision="accept" />;
}
