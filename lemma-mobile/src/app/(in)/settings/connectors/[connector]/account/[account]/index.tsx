import { useLocalSearchParams } from "expo-router";
import { AccountScreen } from "../../../../../../../areas/admin/connectors/screens";

/** One of your accounts on a connector. */
export default function AccountRoute() {
    const params = useLocalSearchParams<{ connector: string; account: string }>();
    return <AccountScreen connectorId={String(params.connector)} accountId={String(params.account)} />;
}
