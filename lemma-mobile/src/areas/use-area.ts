import { useSession } from "../session/session";
import type { Areas } from "./index";

/** One area's source, live or sample as the session is. For a screen that
 *  only renders signed in, which is every screen in the (in) group. */
export function useArea<K extends keyof Areas>(name: K): Areas[K] {
    const { areas } = useSession();
    if (!areas) throw new Error("No source: the app is signed out");
    return areas[name];
}
