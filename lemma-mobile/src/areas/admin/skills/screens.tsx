import { View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma } from "../../../data/hooks";
import { Icon } from "../../../ui/icons";
import { Press } from "../../../ui/press";
import { Counted, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { chatRoutes } from "../../chat/routes";
import { filesRoutes } from "../../files/routes";
import { splitDescription } from "@web/skills/skill-frontmatter";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { Group, ReadProblem, Reading, Row, RowIcon, Say, SettingsScreen, isForbidden } from "../ui";
import { useSkills } from "./hooks";
import { NEW_SKILL_ASK, skillMeta } from "./model";
import { sayUnloadable, type SkillCard } from "@web/skills/skills";

/** A Lemma's skills: what each is for, how long, when it last changed, and
 *  any that will not load, saying why (the web's Skills part of About). A
 *  skill opens as its `SKILL.md` in the file viewer. A new one is not a
 *  form: a new chat asks the Lemma to write it, with the skill whose whole
 *  job that is, and the person reads the words before sending them. */
export function SkillsScreen({ podId }: PartProps) {
    const router = useRouter();
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "this Lemma";
    const part = lemmaPart("skills")!;
    const skills = useSkills(podId);
    const cards = skills.data ?? [];
    const unloadable = sayUnloadable(cards);
    return (
        <SettingsScreen title={part.title(name)} note={part.note(name)} back={lemma?.name ?? "Settings"}>
            <Group foot={unloadable ?? undefined}>
                <Row
                    role="link"
                    glyph={<RowIcon name="plus" />}
                    label="New skill"
                    hint={`Ask ${name} to write one`}
                    last={cards.length === 0 && !skills.isPending}
                    onPress={() => router.push(chatRoutes.filled(podId, NEW_SKILL_ASK) as never)}
                />
                {skills.isPending ? <Reading label="Reading the skills" /> : null}
                {skills.isError ? (
                    <ReadProblem said={isForbidden(skills.error) ? "You may not read these skills." : "Couldn’t load skills."} onRetry={() => void skills.refetch()} />
                ) : null}
                {cards.map((card, index) => (
                    <SkillRow key={card.folder} card={card} last={index === cards.length - 1} onOpen={() => router.push(filesRoutes.file(podId, card.file) as never)} />
                ))}
            </Group>
            {skills.isSuccess && cards.length === 0 ? (
                <Say>{name} has no saved skills yet. Ask it to save what it has learned about a task you repeat.</Say>
            ) : null}
        </SettingsScreen>
    );
}

/** One skill: its name, what it does (or why it will not load, in amber),
 *  and how much instruction it is and when it changed. */
function SkillRow({ card, last, onOpen }: { card: SkillCard; last: boolean; onOpen: () => void }) {
    const does = splitDescription(card.description).does;
    const line = card.problem ?? (does || (card.read ? "No description yet." : ""));
    const meta = skillMeta(card);
    return (
        <Press
            role="link"
            label={[card.title, line, meta].filter(Boolean).join(", ")}
            onPress={onOpen}
            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 58, paddingVertical: 8, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}
        >
            <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Text size={16} numberOfLines={1}>{card.title}</Text>
                {line ? <Text size={13.5} tone={card.problem ? color.amber : color.ink2} numberOfLines={2} style={{ lineHeight: 18 }}>{line}</Text> : null}
                {meta ? <Counted size={12.5} tone={color.ink3}>{meta}</Counted> : null}
            </View>
            <Icon name="forward" size={16} tone={color.ink3} />
        </Press>
    );
}
