import { useLocalSearchParams } from "expo-router";
import { JoinScreen } from "../../../areas/more/begin/not-yours";

/** A Lemma that is not one of yours, and asking to join it. */
export default function Join() {
    const { podId } = useLocalSearchParams<{ podId: string }>();
    return <JoinScreen podId={String(podId ?? "")} />;
}
