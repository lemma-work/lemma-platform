import { Easing, ReduceMotion, type WithSpringConfig, type WithTimingConfig } from "react-native-reanimated";

/** One way things move, everywhere: an object travels to its new place
 *  rather than the screen cutting to another, on one curve and one spring.
 *  With the phone's reduce-motion setting on, both end at once. */

export const MOVE_MS = 480;

/** Settling into place: fast out, long soft landing. */
export const settle: WithTimingConfig = { duration: MOVE_MS, easing: Easing.bezier(0.2, 0.8, 0.2, 1), reduceMotion: ReduceMotion.System };

/** Leaving: quicker, accelerating away. */
export const leave: WithTimingConfig = { duration: 260, easing: Easing.bezier(0.4, 0, 1, 1), reduceMotion: ReduceMotion.System };

/** Something that springs out of where it was touched: a drawer, a sheet. */
export const spring: WithSpringConfig = { damping: 20, stiffness: 190, mass: 1, reduceMotion: ReduceMotion.System };

/** A slow breath, for what is running. */
export const BREATH_MS = 1800;
