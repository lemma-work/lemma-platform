import { TurboModuleRegistry, type TurboModule } from "react-native";

/** Copy text to the phone's clipboard.
 *
 *  React Native's own clipboard module, which is still in the native build on
 *  both platforms, reached through the module registry: its JS wrapper warns
 *  that it has moved out of core whichever way it is imported. `expo-clipboard`
 *  is the long-term home, and it needs a native build, which every area
 *  shares one of; when a build adds it, only this file changes. */
interface NativeClipboard extends TurboModule {
    setString(content: string): void;
}

const clipboard = TurboModuleRegistry.get<NativeClipboard>("Clipboard");

export function copyText(text: string): void {
    clipboard?.setString(text);
}
