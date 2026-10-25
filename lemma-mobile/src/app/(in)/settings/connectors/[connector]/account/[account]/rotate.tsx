import { useLocalSearchParams } from "expo-router";
import { RotateScreen } from "../../../../../../../areas/admin/connectors/screens";

/** Replacing an account's credential, keeping the account. */
export default function RotateRoute() {
    const params = useLocalSearchParams<{ connector: string; account: string }>();
    return <RotateScreen connectorId={String(params.connector)} accountId={String(params.account)} />;
}
