import { Tabs } from "expo-router/js-tabs";
import { useNeeds } from "../../../data/hooks";
import { TabBar } from "../../../ui/tab-bar";
import { color } from "../../../ui/tokens";

/** Home · Needs you · Apps (`ui/tab-bar.tsx`). */
export default function TabsLayout() {
    const { needs } = useNeeds();
    return (
        <Tabs
            screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: color.paper } }}
            tabBar={(props) => <TabBar {...props} needs={needs.length} />}
        >
            <Tabs.Screen name="(home)" />
            <Tabs.Screen name="needs" />
            <Tabs.Screen name="apps" />
        </Tabs>
    );
}
