import { useState } from "react";
import { useLocalSearchParams } from "expo-router";
import { useApps, useLemma } from "../../../../data/hooks";
import { readableName } from "../../../../data/model";
import { useSession } from "../../../../session/session";
import { SketchOf } from "../../../../ui/app-tile";
import { Loading } from "../../../../ui/chrome";
import { Press } from "../../../../ui/press";
import { ShareSheet } from "../../../../areas/more/share/sheet";
import { FilesIcon } from "../../../../areas/files/glyphs";
import { Framed } from "../../../../ui/framed";
import { Text } from "../../../../ui/text";
import { color, TOUCH } from "../../../../ui/tokens";

/** A pod app, full screen. */
export default function AppScreen() {
    const params = useLocalSearchParams<{ podId: string; name: string }>();
    const podId = String(params.podId);
    const name = String(params.name);
    const { mode } = useSession();
    const lemma = useLemma(podId);
    const { apps, pending } = useApps();
    const app = apps.find((one) => one.podId === podId && one.name === name) ?? null;
    const title = app?.label ?? readableName(name);
    const [sharing, setSharing] = useState(false);
    /* Who can open it and its address (area G). */
    const share = (
        <>
            <Press label={"Share " + title} onPress={() => setSharing(true)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                <FilesIcon name="share" size={19} tone={color.ink2} />
            </Press>
            <ShareSheet podId={podId} subject={{ kind: "app", name, label: title }} open={sharing} onClose={() => setSharing(false)} />
        </>
    );
    if (pending) return <Framed url={null} title={title} right={share}><Loading /></Framed>;
    return (
        <Framed url={app?.url ?? null} title={title} subtitle={lemma?.name} right={share}>
            <SketchOf name={name} height={200} />
            <Text size={15.5} tone={color.ink2} style={{ lineHeight: 22 }}>
                {mode === "sample"
                    ? "In sample data, apps have no address to open. Sign in, and each app opens here full screen."
                    : "This app has no address yet. It may still be deploying."}
            </Text>
        </Framed>
    );
}
