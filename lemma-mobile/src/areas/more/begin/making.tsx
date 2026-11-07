import { ActivityIndicator, View } from "react-native";
import { Image } from "expo-image";
import { CAST, LemmaFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { identityIcon } from "../../admin/about/faces";
import { characterOf, stepState, stepsFor, type Start, type StepId } from "./model";

/** A new Lemma's face. Before it exists there is no id to draw it from, so
 *  the character its start was dealt is drawn on a quiet wash; once it does,
 *  its own face, as every other screen will draw it. */
export function StartFace({ start, made, size }: { start: Start; made?: { id: string; variant: number } | null; size: number }) {
    if (made) return <LemmaFace id={made.id} icon={made.variant > 0 ? identityIcon(made.variant) : null} size={size} />;
    const inner = Math.round(size * 0.86);
    return (
        <View
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={{ width: size, height: size, borderRadius: Math.round(size * 0.27), backgroundColor: color.wash, alignItems: "center", justifyContent: "center", overflow: "hidden" }}
        >
            <Image source={CAST[characterOf(start)]} style={{ width: inner, height: inner }} contentFit="contain" />
        </View>
    );
}

/** The wait, said as what it is: each step that really happens, ticked as
 *  it finishes, the one under way turning, and where a failure stopped it. */
export function Making({ start, done, failed }: { start: Start; done: readonly StepId[]; failed: boolean }) {
    const steps = stepsFor(start);
    return (
        <View style={{ gap: 18 }}>
            <StartFace start={start} size={72} />
            <View accessibilityRole="list">
                {steps.map((step, index) => {
                    const state = stepState(step.id, steps, done, failed);
                    return (
                        <View
                            key={step.id}
                            accessibilityLabel={`${step.label}, ${state === "done" ? "done" : state === "running" ? "under way" : state === "stopped" ? "stopped" : "waiting"}`}
                            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 48, borderBottomWidth: index === steps.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                        >
                            <View style={{ width: 22, alignItems: "center" }}>
                                {state === "done" ? <Icon name="check" size={17} width={2.2} /> : null}
                                {state === "running" ? <ActivityIndicator size="small" color={color.violet} /> : null}
                                {state === "stopped" ? <Icon name="close" size={15} width={2.2} tone={color.amber} /> : null}
                                {state === "waiting" ? <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: color.line2 }} /> : null}
                            </View>
                            <Text size={16} tone={state === "waiting" ? color.ink3 : state === "stopped" ? color.amber : color.ink} style={{ flex: 1 }}>{step.label}</Text>
                        </View>
                    );
                })}
            </View>
        </View>
    );
}
