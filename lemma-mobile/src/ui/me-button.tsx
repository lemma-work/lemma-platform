import { useRouter } from "expo-router";
import { useMe } from "../data/hooks";
import { routes } from "../data/routes";
import { TourTarget } from "../areas/more/tour/target";
import { PersonFace } from "./face";
import { Press } from "./press";

/** Your face, top right on every tab: you, Connectors and Settings, as the
 *  web keeps them at the foot of its sidebar. */
export function MeButton() {
    const router = useRouter();
    const me = useMe();
    return (
        <TourTarget id="you">
            <Press label="You, Connectors and Settings" onPress={() => router.push(routes.account as never)} hitSlop={8}>
                <PersonFace name={me.data?.name ?? "You"} size={34} dark />
            </Press>
        </TourTarget>
    );
}
