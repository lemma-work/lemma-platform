import { useLocalSearchParams } from "expo-router";
import { ContactScreen } from "../../../../../areas/chats/contact";

/** One contact of a Lemma (PARITY.md, E). */
export default function Contact() {
    const params = useLocalSearchParams<{ podId: string; id: string }>();
    return <ContactScreen podId={String(params.podId)} contactId={String(params.id)} />;
}
