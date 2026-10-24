import * as Haptics from "expo-haptics";

/** Three feelings, named for what they mean rather than how they feel. */
export const haptics = {
    /** A layout snapped, a choice moved. */
    tick: () => void Haptics.selectionAsync().catch(() => undefined),
    /** Something opened under the finger: a drawer, a hold that began. */
    firm: () => void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => undefined),
    /** It is done: approved, sent. */
    done: () => void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined),
};
