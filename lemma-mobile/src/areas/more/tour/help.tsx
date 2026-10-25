import { useState } from "react";
import { View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useLemmas } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import { useSession } from "../../../session/session";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { ChoiceRow, Group, Row, Say, SettingsScreen } from "../../admin/ui";
import { problemOf } from "../../admin/words";
import { chatRoutes } from "../../chat/routes";
import { useFilesSource } from "../../files/hooks";
import { filesRoutes } from "../../files/routes";
import { PAGE_TEMPLATES } from "@web/docpages/templates";
import { moreRoutes } from "../routes";
import { askForTour } from "./bus";
import { GUIDE_PLACES, guideFor, guideTitle, isGuidePlace, type GuidePlace } from "./model";

/** Help, on the account screen (the web's help menu): the tour again, how
 *  each place works, and the two pages on the web that explain the rest.
 *  The tour runs over Home, so asking for it goes back to Home, past
 *  whatever else is open, and starts once Home is in front. */
export function HelpGroup() {
    const router = useRouter();
    const { server } = useSession();
    return (
        <Group label="Help">
            <Row
                label="Show me around"
                hint="A minute on where everything is"
                onPress={() => {
                    router.dismissTo(routes.home as never);
                    setTimeout(askForTour, 450);
                }}
            />
            {GUIDE_PLACES.map((place) => (
                <Row key={place} label={guideTitle(place)} onPress={() => router.push(moreRoutes.guide(place) as never)} />
            ))}
            <Row label="Documentation" value={server.web.replace(/^https?:\/\//, "")} onPress={() => void WebBrowser.openBrowserAsync(server.web + "/docs")} />
            <Row label="What’s new" onPress={() => void WebBrowser.openBrowserAsync(server.web + "/changelog")} last />
        </Group>
    );
}

/** How a place works (`(in)/guide/[place]`): its gestures, one line each,
 *  and a way to try it in one of the Lemmas, which never sends anything. */
export function GuideScreen() {
    const params = useLocalSearchParams<{ place?: string; pod?: string }>();
    const place: GuidePlace = isGuidePlace(params.place) ? params.place : "pages";
    const { lemmas } = useLemmas();
    const [chosen, setChosen] = useState<string | null>(typeof params.pod === "string" && params.pod ? params.pod : null);
    const podId = chosen ?? lemmas[0]?.id ?? null;
    const lemma = lemmas.find((one) => one.id === podId) ?? null;
    const guide = guideFor(place, lemma?.name ?? null);
    return (
        <SettingsScreen title={guide.title} back="Account">
            <View>
                {guide.lines.map((line, index) => (
                    <View key={line.gesture} style={{ paddingVertical: 12, gap: 3, borderBottomWidth: index === guide.lines.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                        <Text size={15.5} weight="medium">{line.gesture}</Text>
                        <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{line.what}</Text>
                    </View>
                ))}
            </View>
            {guide.tryIt && lemmas.length > 0 && podId ? (
                <Group label="Try it">
                    {lemmas.length > 1 ? (
                        <ChoiceRow
                            label="In"
                            title="Try it in"
                            choices={lemmas.map((one) => ({ value: one.id, label: one.name }))}
                            value={podId}
                            onPick={setChosen}
                        />
                    ) : null}
                    <TryIt place={place} podId={podId} lemmaName={lemma?.name ?? "the Lemma"} />
                </Group>
            ) : null}
        </SettingsScreen>
    );
}

function TryIt({ place, podId, lemmaName }: { place: GuidePlace; podId: string; lemmaName: string }) {
    const router = useRouter();
    const files = useFilesSource();
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const tryIt = guideFor(place, lemmaName).tryIt;
    if (!tryIt) return null;

    /* The page that explains pages by being one: opened if this Lemma has
       it already, made from its template if not, as on the web. */
    const openPageGuide = async () => {
        const template = PAGE_TEMPLATES.find((one) => one.id === "guide");
        if (!template) return;
        setBusy(true);
        setProblem(null);
        try {
            const wanted = template.file + ".md";
            let path: string | null = null;
            try {
                const found = (await files.list(podId, "/pages")).items.find((item) => item.name.toLowerCase() === wanted.toLowerCase());
                path = found?.path ?? null;
            } catch {
                /* No listing: make one. */
            }
            if (!path) {
                path = "/pages/" + wanted;
                await files.createText(podId, path, template.body(lemmaName, new Date()));
            }
            router.push(filesRoutes.file(podId, path) as never);
        } catch (error) {
            setProblem(problemOf(error, "The guide page could not be opened just now."));
        } finally {
            setBusy(false);
        }
    };

    return (
        <View style={{ gap: 10, paddingTop: 10 }}>
            {tryIt.kind === "ask" ? (
                <Text size={14} tone={color.ink3} style={{ lineHeight: 19 }}>Opens a new chat with {lemmaName}, with the words in the box. Nothing sends until you do.</Text>
            ) : (
                <Text size={14} tone={color.ink3} style={{ lineHeight: 19 }}>A page in {lemmaName}’s Files that explains pages by being one.</Text>
            )}
            <View style={{ flexDirection: "row" }}>
                <Button
                    title={tryIt.label}
                    busy={busy}
                    onPress={() => (tryIt.kind === "ask" ? router.push(chatRoutes.filled(podId, tryIt.text) as never) : void openPageGuide())}
                />
            </View>
            {problem ? <Say tone="bad">{problem}</Say> : null}
        </View>
    );
}
