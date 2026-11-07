/** Just enough of `react-native` for the pure modules under test. */
export const Platform = { OS: "android" as const, select: <T,>(options: { android?: T; default?: T }) => options.android ?? options.default };
