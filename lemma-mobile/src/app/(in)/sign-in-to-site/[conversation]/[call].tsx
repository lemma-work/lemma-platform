import { useLocalSearchParams } from "expo-router";
import { SignInScreen } from "../../../../areas/more/computer/sign-in";

/** A site waiting on a person to sign in, at the web's own path
 *  (`/sign-in-to-site/{conversation}/{call}`, `moreRoutes.signIn`), so a
 *  link in the web's grammar opens it here. */
export default function SignInToSite() {
    const { conversation, call } = useLocalSearchParams<{ conversation: string; call: string }>();
    return <SignInScreen key={`${conversation}/${call}`} conversationId={String(conversation ?? "")} toolCallId={String(call ?? "")} />;
}
