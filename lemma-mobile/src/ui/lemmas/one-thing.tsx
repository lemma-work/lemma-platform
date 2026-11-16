import { useState } from "react";
import { useRouter } from "expo-router";
import { useAnswer, useCard } from "../../data/hooks";
import { hrefOfNeed } from "../../data/model";
import { SAMPLE_SHORT } from "../../data/sample";
import type { NeedItem } from "../../data/types";
import { useSession } from "../../session/session";
import { haptics } from "../haptics";
import { Button, Press } from "../press";
import { Counted } from "../text";
import { color } from "../tokens";

/** The one thing a Lemma needs from you, on one line, with its yes; on its
 *  page and in its drawer. Anything that is not a plain yes opens. */
export function OneThing({ item, onOpen }: { item: NeedItem; onOpen?: () => void }) {
    const router = useRouter();
    const { mode } = useSession();
    const title = mode === "sample" ? SAMPLE_SHORT[item.key] ?? item.title : item.title;
    const card = useCard(item);
    const answer = useAnswer();
    const [done, setDone] = useState<string | null>(null);
    const offer = card.data?.answer;
    const open = () => {
        onOpen?.();
        router.push(hrefOfNeed(item) as never);
    };
    return (
        <Press role="link" label={title} onPress={open} style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 14, backgroundColor: color.violetWash }}>
            <Counted size={15} numberOfLines={2} style={{ flex: 1, minWidth: 0 }}>{done ?? title}</Counted>
            {done ? null : offer?.kind === "approve" ? (
                <Button title={offer.approve} height={34} size={14} busy={answer.isPending} onPress={() => answer.mutate({ item, decision: "APPROVE_ONCE" }, { onSuccess: () => { haptics.done(); setDone("Approved."); } })} />
            ) : (
                <Button title={offer?.kind === "open" ? offer.label : "Answer"} height={34} size={14} onPress={open} />
            )}
        </Press>
    );
}
