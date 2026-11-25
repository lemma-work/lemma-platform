import type { ReactNode } from "react";
import { View } from "react-native";
import type { Lemma } from "../data/types";
import { LemmaFace } from "./face";
import { Text } from "./text";
import { color } from "./tokens";

/** The web's `Calm` (`outcomes/calm.tsx`): a quiet middle for a page with
 *  nothing in it, or one whose read failed -- what is true, said plainly,
 *  and the one thing to do next. Faces only for an empty page, never for a
 *  failure: a read failing is the connection's doing, not a Lemma's. */
export function Calm({ title, line, faces = [], children }: { title: string; line?: string; faces?: readonly Pick<Lemma, "id" | "iconUrl">[]; children?: ReactNode }) {
    return (
        <View style={{ alignItems: "center", gap: 8, paddingHorizontal: 28, paddingTop: 40, paddingBottom: 24 }}>
            {faces.length > 0 ? (
                <View style={{ flexDirection: "row", marginBottom: 8 }}>
                    {faces.map((one, index) => (
                        <View key={one.id} style={{ marginLeft: index === 0 ? 0 : -8 }}>
                            <LemmaFace id={one.id} icon={one.iconUrl} size={40} />
                        </View>
                    ))}
                </View>
            ) : null}
            <Text size={18} weight="medium" style={{ textAlign: "center" }}>{title}</Text>
            {line ? <Text size={14.5} tone={color.ink2} style={{ textAlign: "center", lineHeight: 20.5 }}>{line}</Text> : null}
            {children ? <View style={{ marginTop: 8 }}>{children}</View> : null}
        </View>
    );
}
