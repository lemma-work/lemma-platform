import { useLocalSearchParams } from "expo-router";
import { ComingScreen } from "../../../ui/coming";
import { SETTINGS_SCREENS } from "../../../areas/admin/screens";
import { settingsEntry } from "../../../areas/admin/sections";

/** One part of the account's or the organization's settings, drawn by its
 *  own component (`areas/admin/screens.tsx`). */
export default function SettingsSection() {
    const { section } = useLocalSearchParams<{ section: string }>();
    const found = settingsEntry(section);
    if (!found) return <ComingScreen title="Settings" say="There is no such setting." podId="" tail="/t" back="Account" />;
    const Screen = SETTINGS_SCREENS[found.id];
    return <Screen />;
}
