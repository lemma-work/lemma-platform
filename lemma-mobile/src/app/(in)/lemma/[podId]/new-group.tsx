import { useLocalSearchParams } from "expo-router";
import { NewGroupScreen } from "../../../../areas/chats/new-group";

/** A new group with people in the Lemma (PARITY.md, E). */
export default function NewGroup() {
    const params = useLocalSearchParams<{ podId: string }>();
    return <NewGroupScreen podId={String(params.podId)} />;
}
