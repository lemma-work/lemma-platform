import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useColorScheme } from "react-native";
import * as SystemUI from "expo-system-ui";
import { store } from "../session/store";
import { applyScheme, color, type Scheme } from "./tokens";

/** Light, dark, or as the phone is: the web's Appearance setting.
 *
 *  `color` (ui/tokens.ts) holds the palette in use, and screens read it as
 *  they draw, so a change of scheme is applied before anything below draws
 *  again, and the layout remounts its navigator on `scheme` so every screen
 *  draws again. No screen subscribes to anything. */

export type Appearance = "system" | "light" | "dark";

interface Theme {
    appearance: Appearance;
    scheme: Scheme;
    setAppearance: (appearance: Appearance) => void;
}

const ThemeContext = createContext<Theme | null>(null);

let applied: Scheme = "light";

export function ThemeProvider({ children }: { children: ReactNode }) {
    const system = useColorScheme();
    const [appearance, setChosen] = useState<Appearance>("system");
    useEffect(() => {
        void store.appearance().then(setChosen);
    }, []);
    const scheme: Scheme = appearance === "system" ? (system === "dark" ? "dark" : "light") : appearance;
    /* Before the children draw, not in an effect after: they read `color`
       on this very render. */
    if (applied !== scheme) {
        applyScheme(scheme);
        applied = scheme;
    }
    useEffect(() => {
        void SystemUI.setBackgroundColorAsync(color.paper).catch(() => undefined);
    }, [scheme]);
    const setAppearance = useCallback((next: Appearance) => {
        setChosen(next);
        void store.setAppearance(next);
    }, []);
    const value = useMemo(() => ({ appearance, scheme, setAppearance }), [appearance, scheme, setAppearance]);
    return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
    const theme = useContext(ThemeContext);
    if (!theme) throw new Error("useTheme outside ThemeProvider");
    return theme;
}
