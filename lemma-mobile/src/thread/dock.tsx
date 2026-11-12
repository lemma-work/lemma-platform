import { useState, type MutableRefObject, type ReactNode } from "react";
import { ScrollView, useWindowDimensions } from "react-native";
import Animated, { withSpring, withTiming, type EntryAnimationsValues, type EntryExitAnimationFunction, type ExitAnimationsValues } from "react-native-reanimated";
import { leave, spring } from "../ui/motion";
import { color } from "../ui/tokens";
import { SignInCard } from "./cards";
import { InteractionCard } from "./interaction";
import type { Interaction, OpenSignIn } from "@web/thread/turns";
import type { AnswerWith, Resolve } from "./use-chat";

/** The shelf between the chat and the composer, holding whatever the run is
 *  blocked on: the web's `interaction-dock.tsx`. An approval or a question
 *  is the one thing to do in this chat until it is answered, so it sits
 *  where the thumbs are, at the composer's width, rather than wherever it
 *  streamed in. Only while it is open: answered, the record is in the chat
 *  where it was asked. It folds itself while the draft below is long, and
 *  can be folded by hand to read the chat above. */
/** Up from below its own height, on the one spring. */
const rise: EntryExitAnimationFunction = (values: EntryAnimationsValues) => {
    "worklet";
    return {
        initialValues: { opacity: 0, transform: [{ translateY: values.targetHeight + 24 }] },
        animations: { opacity: withTiming(1, { duration: 160 }), transform: [{ translateY: withSpring(0, spring) }] },
    };
};

const drop: EntryExitAnimationFunction = (values: ExitAnimationsValues) => {
    "worklet";
    return {
        initialValues: { opacity: 1, transform: [{ translateY: 0 }] },
        animations: { opacity: withTiming(0, leave), transform: [{ translateY: withTiming(values.currentHeight + 24, leave) }] },
    };
};

/** Each pause is a card of its own that springs up from behind the composer
 *  and drops back down when it is answered. */
function Card({ id, children, max }: { id: string; children: ReactNode; max?: number }) {
    return (
        <Animated.View
            key={id}
            entering={rise}
            exiting={drop}
            style={{
                maxHeight: max,
                marginHorizontal: 8,
                marginBottom: 2,
                borderWidth: 1,
                borderColor: color.line2,
                borderRadius: 20,
                backgroundColor: color.paper,
                overflow: "hidden",
                elevation: 6,
                shadowColor: color.photo,
                shadowOpacity: 0.08,
                shadowRadius: 14,
                shadowOffset: { width: 0, height: -2 },
            }}
        >
            {children}
        </Animated.View>
    );
}

export function InteractionDock({ interaction, name, onResolve, runEnded, resumesOnAnswer, signIn, podId, conversationId, crowded, answerWith }: {
    interaction: Interaction | null;
    name: string;
    onResolve: Resolve;
    runEnded: boolean;
    resumesOnAnswer: boolean;
    signIn: OpenSignIn | null;
    podId: string;
    conversationId: string | null;
    crowded: boolean;
    answerWith: MutableRefObject<AnswerWith | null>;
}) {
    const { height } = useWindowDimensions();
    /* Per pause, so the next question does not arrive folded. A draft
       growing long, or emptying, is a newer wish than the last tap. */
    const [foldedFor, setFoldedFor] = useState<Record<string, boolean>>({});
    const [crowdedWas, setCrowdedWas] = useState(crowded);
    if (crowdedWas !== crowded) {
        setCrowdedWas(crowded);
        setFoldedFor({});
    }
    if (!interaction) {
        if (!signIn) return null;
        return (
            <Card id={"sign-in:" + signIn.toolCallId}>
                <SignInCard ask={signIn.card} podId={podId} conversationId={conversationId} toolCallId={signIn.toolCallId} docked />
            </Card>
        );
    }
    const folded = foldedFor[interaction.id] ?? crowded;
    return (
        <Card id={interaction.id} max={Math.round(height * 0.56)}>
            <ScrollView keyboardShouldPersistTaps="handled" bounces={false}>
                <InteractionCard
                    key={interaction.id}
                    interaction={interaction}
                    name={name}
                    onResolve={onResolve}
                    runEnded={runEnded}
                    resumesOnAnswer={resumesOnAnswer}
                    docked
                    folded={folded}
                    onFold={(next) => setFoldedFor((was) => ({ ...was, [interaction.id]: next }))}
                    answerWith={answerWith}
                />
            </ScrollView>
        </Card>
    );
}
