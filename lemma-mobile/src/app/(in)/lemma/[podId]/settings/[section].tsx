import { useLocalSearchParams } from "expo-router";
import { ComingScreen } from "../../../../../ui/coming";
import { LEMMA_SCREENS } from "../../../../../areas/admin/screens";
import { lemmaPart } from "../../../../../areas/admin/sections";

/** One part of a Lemma's settings, drawn by its own component
 *  (`areas/admin/screens.tsx`); "about" is its name and face. */
export default function LemmaSettingsSection() {
    const params = useLocalSearchParams<{ podId: string; section: string }>();
    const podId = String(params.podId);
    const id = params.section === "about" ? "about" : lemmaPart(params.section)?.id;
    if (!id) return <ComingScreen title="Settings" say="There is no such setting." podId={podId} tail="/t" back="Settings" />;
    const Screen = LEMMA_SCREENS[id];
    return <Screen podId={podId} />;
}
