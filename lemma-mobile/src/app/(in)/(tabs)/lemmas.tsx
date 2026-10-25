import { useEffect, useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { runOnJS } from "react-native-reanimated";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNeeds, usePulse, useSections } from "../../../data/hooks";
import type { LemmaSection } from "../../../data/types";
import { moreRoutes } from "../../../areas/more/routes";
import { store } from "../../../session/store";
import { Empty, IconButton, Loading, PageTitle } from "../../../ui/chrome";
import { haptics } from "../../../ui/haptics";
import { Icon } from "../../../ui/icons";
import { FacesBoard, type Origin } from "../../../ui/lemmas/faces-board";
import { LemmaDrawer } from "../../../ui/lemmas/lemma-drawer";
import { heroHref } from "../../../ui/lemmas/hero";
import { Press } from "../../../ui/press";
import { color } from "../../../ui/tokens";

/** Every Lemma, as faces or as standings. One set of faces in two layouts:
 *  the toggle or a pinch moves each face to its place in the other. A tap
 *  lifts the face into a drawer of what it owns, needs and did lately. */
export default function Lemmas() {
    const router = useRouter();
    const { sections, pending } = useSections();
    const { refetch, fetching } = usePulse();
    const { needs } = useNeeds();
    const [mode, setMode] = useState<"grid" | "list">("grid");
    const [open, setOpen] = useState<{ section: LemmaSection; origin: Origin } | null>(null);
    const [leaving, setLeaving] = useState(false);
    useEffect(() => {
        void store.lemmasView().then(setMode);
    }, []);
    const choose = (next: "grid" | "list") => {
        if (next === mode) return;
        haptics.tick();
        setMode(next);
        void store.setLemmasView(next);
    };
    const pinch = Gesture.Pinch().onEnd((event) => {
        if (event.scale < 0.88) runOnJS(choose)("list");
        else if (event.scale > 1.12) runOnJS(choose)("grid");
    });
    return (
        <SafeAreaView edges={["top"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ paddingTop: 12 }}>
                <PageTitle right={<>
                    <IconButton name="search" label="Search" onPress={() => router.push(moreRoutes.search as never)} />
                    <View accessibilityRole="radiogroup" style={{ flexDirection: "row", borderWidth: 1, borderColor: color.line2, borderRadius: 18, padding: 2, marginLeft: 4 }}>
                        {(["grid", "list"] as const).map((one) => (
                            <Press key={one} role="radio" label={one === "grid" ? "Faces" : "Standings"} accessibilityState={{ selected: mode === one }} onPress={() => choose(one)} style={{ width: 38, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: mode === one ? color.ink : "transparent" }}>
                                <Icon name={one === "grid" ? "lemmas" : "list"} size={16} tone={mode === one ? color.onInk : color.ink2} />
                            </Press>
                        ))}
                    </View>
                </>}>Lemmas</PageTitle>
            </View>
            <GestureDetector gesture={pinch}>
                <ScrollView
                    contentContainerStyle={{ paddingTop: 10, paddingBottom: 32 }}
                    refreshControl={<RefreshControl refreshing={fetching && !pending} onRefresh={() => void refetch()} tintColor={color.ink3} />}
                >
                    {pending ? <Loading /> : null}
                    {!pending && sections.length === 0 ? <Empty title="No Lemmas here yet." /> : null}
                    {sections.length > 0 ? (
                        <FacesBoard
                            sections={sections}
                            mode={mode}
                            onOpen={(section, origin) => setOpen({ section, origin })}
                needs={needs}
                            onNew={() => router.push(moreRoutes.newLemma as never)}
                        />
                    ) : null}
                </ScrollView>
            </GestureDetector>
            <LemmaDrawer
                section={open?.section ?? null}
                origin={open?.origin ?? null}
                onClose={() => setOpen(null)}
                leaving={leaving}
                onOpen={(section, from) => {
                    /* The drawer goes first, at once; the page opens on the next
                       frame, once this screen is no longer drawing a drawer. A
                       screen in the background stops updating, so a drawer left
                       to slide away would hang over the page. */
                    setLeaving(true);
                    setOpen(null);
                    requestAnimationFrame(() => {
                        router.push(heroHref(section.lemma.id, from) as never);
                        setLeaving(false);
                    });
                }}
            />
        </SafeAreaView>
    );
}
