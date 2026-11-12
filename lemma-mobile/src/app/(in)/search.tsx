import { useLocalSearchParams } from "expo-router";
import { SearchScreen } from "../../areas/more/search/screens";

/** Search across every Lemma: chats, files, records, apps, agents,
 *  workflows, functions, tables, people and schedules (PARITY.md, G).
 *  `?pod=` starts it on one Lemma (`moreRoutes.searchIn`). */
export default function Search() {
    const params = useLocalSearchParams<{ pod?: string }>();
    return <SearchScreen podId={params.pod ? String(params.pod) : null} />;
}
