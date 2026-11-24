import { useLocalSearchParams } from "expo-router";
import { AddPeopleScreen } from "../../../../../../areas/admin/people/screens";

/** Add people to a Lemma (`areas/admin/people`). */
export default function AddPeople() {
    const params = useLocalSearchParams<{ podId: string }>();
    return <AddPeopleScreen podId={String(params.podId)} />;
}
