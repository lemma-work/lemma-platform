import { useLocalSearchParams } from "expo-router";
import { PlaceScreen } from "../../../../../../../areas/admin/reach/place";

/** One place a Lemma answers, by its name (`areas/admin/reach/place.tsx`). */
export default function ReachPlace() {
    const params = useLocalSearchParams<{ podId: string; name: string }>();
    return <PlaceScreen podId={String(params.podId)} name={String(params.name)} />;
}
