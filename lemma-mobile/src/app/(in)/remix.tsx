import { useLocalSearchParams } from "expo-router";
import { RemixScreen } from "../../areas/more/install/screens";

/** Bring an app to a Lemma: `/remix?source=<the app's address>`, the web's
 *  own path. */
export default function Remix() {
    const params = useLocalSearchParams<{ source?: string | string[] }>();
    const source = Array.isArray(params.source) ? params.source[0] : params.source;
    return <RemixScreen source={source ?? null} />;
}
