import "../session/polyfills";
import { useEffect, useState } from "react";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider, initialWindowMetrics } from "react-native-safe-area-context";
import { SessionProvider, useSession } from "../session/session";
import { WebSignOut } from "../session/web-session";
import { ThemeProvider, useTheme } from "../ui/theme";
import { color } from "../ui/tokens";

void SplashScreen.preventAutoHideAsync().catch(() => undefined);

function Routes() {
    const { mode } = useSession();
    const { scheme } = useTheme();
    const ready = mode !== "loading";
    useEffect(() => {
        if (ready) void SplashScreen.hideAsync().catch(() => undefined);
    }, [ready]);
    /* The navigator renders from the first frame, under the splash screen,
       which stays up until the session is read: a layout that rendered
       something else first left expo-router's linking updating a navigator
       that had not mounted. That is safe for text because the fonts are built
       into the app (`expo-font` in app.json) and named the way each platform
       knows them (`font` in ui/tokens.ts), so nothing is ever laid out in the
       fallback face and then clipped when Albert Sans arrives. */
    const inside = mode === "live" || mode === "sample";
    return (
        <>
            <StatusBar style={scheme === "dark" ? "light" : "dark"} />
            {/* Remounted on a new scheme so every screen draws in it; the router
                opens it again where the person was. */}
            <Stack key={scheme} screenOptions={{ headerShown: false, contentStyle: { backgroundColor: color.paper }, animation: "slide_from_right" }}>
                {/* Everything signed in lives in the (in) group, so a screen added
                    there is guarded without being listed here. */}
                <Stack.Protected guard={inside}>
                    <Stack.Screen name="(in)" />
                </Stack.Protected>
                <Stack.Protected guard={!inside}>
                    <Stack.Screen name="sign-in" />
                </Stack.Protected>
            </Stack>
            <WebSignOut />
        </>
    );
}

/* The safe-area provider starts from the window's insets, so the first screen
   of a cold start is not drawn once with its header under the status bar. */
export default function RootLayout() {
    const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } }));
    return (
        <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider initialMetrics={initialWindowMetrics}>
            <QueryClientProvider client={client}>
                <SessionProvider>
                    <ThemeProvider>
                        <Routes />
                    </ThemeProvider>
                </SessionProvider>
            </QueryClientProvider>
        </SafeAreaProvider>
        </GestureHandlerRootView>
    );
}
