import { Tabs } from "expo-router/js-tabs";
import { useNeeds, useSections } from "../../../data/hooks";
import { TabBar } from "../../../ui/tab-bar";
import { color } from "../../../ui/tokens";

export default function TabsLayout() {
    const { needs } = useNeeds();
    const { sections } = useSections();
    const lemmasWaiting = sections.some((section) => section.waiting > 0);
    return (
        <Tabs
            screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: color.paper } }}
            tabBar={(props) => <TabBar {...props} needs={needs.length} lemmasWaiting={lemmasWaiting} />}
        >
            <Tabs.Screen name="(home)" />
            <Tabs.Screen name="lemmas" />
            <Tabs.Screen name="apps" />
        </Tabs>
    );
}
