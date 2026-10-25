import { useLocalSearchParams } from "expo-router";
import { ConnectScreen } from "../../../../../../../areas/admin/reach/connect";

/** Connecting one place to a Lemma (`areas/admin/reach/connect.tsx`). */
export default function ReachAdd() {
    const params = useLocalSearchParams<{ podId: string; platform: string; taken?: string }>();
    return <ConnectScreen podId={String(params.podId)} platform={String(params.platform).toUpperCase()} taken={params.taken === "1"} />;
}
