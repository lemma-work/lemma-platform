import { View } from "react-native";
import { useRouter } from "expo-router";
import { Button, Press } from "../../../ui/press";
import { Icon } from "../../../ui/icons";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Group, Row, Say } from "../../admin/ui";
import type { Started } from "./hooks";
import { StartFace } from "./making";
import { firstTasks, nextFor, ownedLanding, readyWords, standInWords, type Start } from "./model";

/** The blank start's ending, in place (the web's reveal): its face, whether
 *  it can reply yet, the way in, and the three things that make a new
 *  Lemma real: something to do, somewhere to be reached, and somebody else
 *  who can use it. Each first task opens a chat with the words in the box,
 *  sent by nobody: a first message carries the person's name, so it is
 *  theirs to send. */
export function Met({ start, started, orgName }: { start: Extract<Start, { kind: "blank" }>; started: Started; orgName: string }) {
    const router = useRouter();
    const name = started.made.name;
    const standIn = started.made.standsIn ?? null;
    const podId = standIn?.id ?? started.made.id;
    const words = readyWords(name, start.job, started.needsModel);
    const tasks = firstTasks(start.job);

    return (
        <View style={{ gap: 22 }}>
            <View style={{ gap: 14 }}>
                <StartFace start={start} made={{ id: started.made.id, variant: started.variant }} size={88} />
                <View style={{ gap: 6 }}>
                    <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24, lineHeight: 29 }}>{words.heading}</Text>
                    {words.line ? <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{words.line}</Text> : null}
                </View>
                {started.needsModel ? (
                    <View style={{ flexDirection: "row" }}>
                        <Button title="Set up a model" kind="outline" onPress={() => router.push(nextFor.models() as never)} />
                    </View>
                ) : null}
                {!started.faceSaved ? <Say>{`Couldn’t save the face it was given, so ${name} kept the one it came with.`}</Say> : null}
                {standIn ? <Say>{standInWords(name, standIn.name, false)}</Say> : null}
                <Button title={`Open ${standIn?.name ?? name}`} height={48} onPress={() => router.replace(nextFor.open(podId) as never)} />
            </View>

            <Group label={words.tasks} foot="Opens as a draft. Nothing sends until you do.">
                {tasks.map((line, index) => (
                    <Press
                        key={line}
                        role="link"
                        label={line}
                        onPress={() => router.push(nextFor.task(podId, line) as never)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, paddingVertical: 8, borderBottomWidth: index === tasks.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                    >
                        <Text size={15.5} numberOfLines={2} style={{ flex: 1, lineHeight: 21 }}>{line}</Text>
                        <Icon name="forward" size={16} tone={color.ink3} />
                    </Press>
                ))}
            </Group>

            <Group>
                <Row label="Give it a way to be reached" hint="Message it here in Lemma, or connect email, Slack or WhatsApp." onPress={() => router.push(nextFor.reach(podId) as never)} />
                <Row label="Bring people in" hint={`Invite people from ${orgName} to work with ${name}.`} onPress={() => router.push(nextFor.people(podId) as never)} last />
            </Group>
        </View>
    );
}

/** An owned start's ending, drawn only when it cannot simply open the chat
 *  where the Lemma sets itself up: the organization has no model it can
 *  answer with, or sample data had to stand another Lemma in for it. */
export function OwnedEnding({ start, started }: { start: Extract<Start, { kind: "own" }>; started: Started }) {
    const router = useRouter();
    const name = started.made.name;
    const standIn = started.made.standsIn ?? null;
    return (
        <View style={{ gap: 14 }}>
            <StartFace start={start} made={{ id: started.made.id, variant: started.variant }} size={88} />
            <View style={{ gap: 6 }}>
                <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24, lineHeight: 29 }}>
                    {started.needsModel ? `${name} needs an AI model before it can reply.` : `${name} is setting itself up`}
                </Text>
                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>
                    {started.needsModel ? "It has been asked to set itself up, and can answer once this organization has a model." : "It was asked to build what it needs to own this, and to write its plan."}
                </Text>
            </View>
            {started.needsModel ? (
                <View style={{ flexDirection: "row" }}>
                    <Button title="Set up a model" kind="outline" onPress={() => router.push(nextFor.models() as never)} />
                </View>
            ) : null}
            {!started.faceSaved ? <Say>{`Couldn’t save the face it was given, so ${name} kept the one it came with.`}</Say> : null}
            {standIn ? <Say>{standInWords(name, standIn.name, true)}</Say> : null}
            <Button title="Open the chat" height={48} onPress={() => router.replace(ownedLanding(started.made, started.conversationId, started.asked ?? "") as never)} />
        </View>
    );
}
