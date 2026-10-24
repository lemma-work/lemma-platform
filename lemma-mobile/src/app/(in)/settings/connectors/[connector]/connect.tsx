import { useLocalSearchParams } from "expo-router";
import { ConnectScreen } from "../../../../../areas/admin/connectors/screens";

/** Connecting an account on a connector: on an install when one is named
 *  (`install`), or setting up the organization's own app first (`own`). */
export default function ConnectRoute() {
    const params = useLocalSearchParams<{ connector: string; install?: string; own?: string }>();
    return <ConnectScreen connectorId={String(params.connector)} installId={params.install ? String(params.install) : null} own={params.own === "1"} />;
}
