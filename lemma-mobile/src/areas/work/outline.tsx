import type { ReactNode } from "react";
import { View } from "react-native";
import { Glyph } from "../../thread/glyphs";
import { Text } from "../../ui/text";
import { color, font } from "../../ui/tokens";
import { phoneWords } from "./model";
import { Note } from "./parts";
import type { FlowArm, FlowItem, FlowStep, WorkflowShape } from "@web/workflow/shape";

/** "How it runs": a workflow's steps on one spine, in the order a run meets
 *  them (`workflow/workflows-view.tsx`'s `Shape`). A decision forks into its
 *  arms and the arms close again where they meet; a loop holds its body; a
 *  step reached twice is drawn once and pointed at. The trigger is the
 *  spine's head, filled, because it is what happens before step one.
 *
 *  Drawn for a phone: the spine is a hairline down the left, an arm is the
 *  same spine indented, and nothing scrolls sideways. */
export function Outline({ shape }: { shape: WorkflowShape }) {
    return (
        <View>
            <Spine>
                <Row mark="start">
                    <Text size={15} weight="medium">{shape.start.says}</Text>
                    {shape.start.detail.map((line, at) => <Text key={at} size={13} tone={color.ink3}>{phoneWords(line)}</Text>)}
                </Row>
                <Items items={shape.flow} />
            </Spine>
            {shape.ordered.length === 0 && shape.orphans.length === 0 ? <Note>No steps yet: this workflow has a name and nothing in it.</Note> : null}
            {shape.trouble ? <Note tone={color.amber}>{shape.trouble}</Note> : null}
            {shape.loose.length > 0 ? (
                <>
                    <Text size={12.5} tone={color.ink3} style={{ marginTop: 14, marginBottom: 2, paddingHorizontal: 4 }}>Unconnected steps</Text>
                    {shape.loose.map((items, at) => <Spine key={at}><Items items={items} /></Spine>)}
                </>
            ) : null}
        </View>
    );
}

function Spine({ children }: { children: ReactNode }) {
    return <View style={{ marginLeft: 10, borderLeftWidth: 1, borderLeftColor: color.line2, paddingBottom: 2 }}>{children}</View>;
}

function Row({ mark, children }: { mark: "start" | "step" | "end" | "jump" | "back"; children: ReactNode }) {
    return (
        <View style={{ flexDirection: "row", gap: 12, paddingVertical: 7 }}>
            <View style={{ width: 0, alignItems: "center" }}>
                {mark === "jump" || mark === "back" ? (
                    <Text size={12} tone={color.ink3} style={{ marginLeft: -4, marginTop: 1, backgroundColor: color.paper }}>{mark === "back" ? "↩" : "→"}</Text>
                ) : (
                    <View style={{ marginLeft: -0.5, marginTop: 6, width: 9, height: 9, borderRadius: 5, backgroundColor: mark === "start" ? color.ink2 : color.paper, borderWidth: 1.5, borderColor: mark === "end" ? color.line2 : color.ink2 }} />
                )}
            </View>
            <View style={{ flex: 1, gap: 2, paddingLeft: 6, paddingRight: 4 }}>{children}</View>
        </View>
    );
}

/** A step named the way its own row names it: the author's label, else its id. */
function nameOf(step: FlowStep): string {
    return step.label && step.label !== step.id ? step.label : step.id || "a step";
}

function Code({ children }: { children: string }) {
    return <Text style={{ fontFamily: font.num, fontSize: 12, color: color.ink3 }}>{children}</Text>;
}

function Step({ step, children }: { step: FlowStep; children?: ReactNode }) {
    const named = Boolean(step.label && step.label !== step.id);
    return (
        <Row mark={step.kind === "END" ? "end" : "step"}>
            <Text size={15} tone={step.unreadable ? color.amber : color.ink} style={{ lineHeight: 20 }}>
                {named ? step.label : step.id}
                <Text size={13.5} tone={color.ink2}>{"  "}{step.says}</Text>
            </Text>
            {named && step.id ? <Code>{step.id}</Code> : null}
            {step.detail.map((line, at) => <Text key={at} size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{line}</Text>)}
            {children}
        </Row>
    );
}

function Items({ items }: { items: FlowItem[] }) {
    return (
        <>
            {items.map((item, at) => {
                switch (item.type) {
                    case "step":
                        return <Step key={item.step.id || "step-" + at} step={item.step} />;
                    case "fork":
                        return (
                            <Step key={item.step.id || "fork-" + at} step={item.step}>
                                {item.on ? <Text size={13} tone={color.ink3}>On <Code>{item.on}</Code></Text> : null}
                                <View style={{ gap: 4, paddingTop: 4 }}>
                                    {item.arms.map((arm, one) => <Arm key={one} arm={arm} then={item.then} switched={Boolean(item.on)} />)}
                                </View>
                            </Step>
                        );
                    case "loop":
                        return (
                            <Step key={item.step.id || "loop-" + at} step={item.step}>
                                <View style={{ paddingTop: 4 }}><Spine><Items items={item.body} /></Spine></View>
                            </Step>
                        );
                    case "jump":
                        return (
                            <Row key={"jump-" + at} mark={item.back ? "back" : "jump"}>
                                <Text size={14} tone={color.ink2}>
                                    {item.to ? (item.back ? "Back to " : "On to ") : "Goes to "}
                                    <Text size={14} tone={color.ink}>{item.to ? nameOf(item.to) : item.id || "a step it did not name"}</Text>
                                    {!item.to && item.id ? ", which is not a step here" : ""}
                                </Text>
                            </Row>
                        );
                    case "end":
                        return (
                            <Row key={"end-" + at} mark="end">
                                <Text size={14} tone={color.ink3}>{item.inLoop ? "On to the next one." : "The run ends here."}</Text>
                            </Row>
                        );
                }
            })}
        </>
    );
}

/** One way out of a decision: the condition, then what it leads to. */
function Arm({ arm, then, switched }: { arm: FlowArm; then: FlowStep | null; switched: boolean }) {
    return (
        <View style={{ gap: 2 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <Glyph name="tree" size={13} tone={color.ink3} />
                <Text size={13.5} tone={color.ink2}>{arm.when === null ? "Otherwise" : switched ? "Is " : "If "}{arm.when !== null ? <Code>{arm.when}</Code> : null}</Text>
            </View>
            <Spine>
                {arm.items.length > 0 ? <Items items={arm.items} /> : (
                    <Row mark="jump">
                        <Text size={14} tone={color.ink2}>Straight on{then ? " to " : ""}{then ? <Text size={14} tone={color.ink}>{nameOf(then)}</Text> : null}</Text>
                    </Row>
                )}
            </Spine>
        </View>
    );
}
