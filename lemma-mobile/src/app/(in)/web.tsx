import { useLocalSearchParams } from "expo-router";
import { useLemma } from "../../data/hooks";
import { useSession } from "../../session/session";
import { Framed } from "../../ui/framed";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";

/** A workspace page the phone has no screen of its own for yet -- a table,
 *  a workflow run's form, a Lemma's settings -- opened on the web. */
export default function WebPage() {
    const params = useLocalSearchParams<{ pod?: string; tail?: string; title?: string }>();
    const { mode, server } = useSession();
    const lemma = useLemma(String(params.pod ?? ""));
    const tail = String(params.tail ?? "/");
    const safe = tail.startsWith("/") && !tail.startsWith("//") ? tail : "/";
    const title = String(params.title ?? "Lemma");
    return (
        <Framed url={mode === "live" ? server.web + safe : null} title={title} subtitle={lemma?.name}>
            <Text size={15.5} tone={color.ink2} style={{ lineHeight: 22 }}>
                This opens on the web, where tables, forms and settings live for now. In sample data there is nothing behind it.
            </Text>
        </Framed>
    );
}
