import { useLocalSearchParams } from "expo-router";
import { ContactsScreen } from "../../../../areas/chats/contacts";
import type { ContactsTab } from "../../../../areas/chats/routes";

/** A Lemma's contacts: the inbox, the people, the setup (PARITY.md, E). */
export default function Contacts() {
    const params = useLocalSearchParams<{ podId: string; tab?: string }>();
    return <ContactsScreen podId={String(params.podId)} tab={(params.tab as ContactsTab | undefined) ?? null} />;
}
