import { useCallback, useRef, useState, type ReactNode } from "react";
import { ScrollView, View, type ViewStyle } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { adminRoutes } from "../areas/admin/routes";
import { useChatRepositories } from "../areas/chat/hooks";
import { saidAbout } from "@web/data/trouble";
import { byOwner, FIX_FALLBACK, fixLabel, MORE_INSTALLED, NO_REPOSITORY, repositoryFix, repositoryTitle } from "@web/thread/repositories";
import { Icon } from "../ui/icons";
import { Press } from "../ui/press";
import { Sheet } from "../ui/sheet";
import { Text } from "../ui/text";
import { color } from "../ui/tokens";
import { Glyph } from "./glyphs";

/** The GitHub repository a new chat works in: the web's
 *  `repository-chip.tsx`, as a chip that opens a sheet.
 *
 *  Picked before the chat exists and sent with the conversation it creates:
 *  the agent then works in a worktree of its own, on a branch of its own,
 *  cut from the repository. The sheet lists what every GitHub installation
 *  the organization holds covers for the reader, under each account when
 *  there are several; what it says and how it groups are the web's own
 *  (`@web/thread/repositories`).
 *
 *  When GitHub is connected but not usable yet, the chip says the step that
 *  is missing and opens Connectors over this screen, so what is typed here
 *  stays; the listing is read again on coming back. Any other refusal, or
 *  nothing to offer, draws nothing: a chat in no repository is the ordinary
 *  one. */
export function RepositoryChip({ podId, picked, onPick, row = false }: {
    podId: string;
    /** "owner/name", or null for a chat in no repository. */
    picked: string | null;
    onPick: (repository: string | null) => void;
    /** On a row of its own above a chat's box, outlined; left out, a chip in
     *  the ask box's bar beside the Lemma's. */
    row?: boolean;
}) {
    const router = useRouter();
    const [open, setOpen] = useState(false);
    const listed = useChatRepositories(podId);
    /* Read again on coming back only while refused: the fix is made on
       Connectors, a screen over this one. */
    const refused = useRef(false);
    refused.current = listed.isError;
    const { refetch } = listed;
    useFocusEffect(useCallback(() => {
        if (refused.current) void refetch();
    }, [refetch]));

    const pill: ViewStyle = row
        ? { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1, minWidth: 0, height: 32, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: color.line2 }
        : { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1, minWidth: 0, height: 30, paddingHorizontal: 10, borderRadius: 999, backgroundColor: color.wash };
    const placed = (chip: ReactNode) => (row ? <View style={{ flexDirection: "row", paddingHorizontal: 12, paddingTop: 6 }}>{chip}</View> : chip);

    if (listed.error) {
        const fix = repositoryFix(listed.error);
        if (!fix) return null;
        return placed(
            <Press
                role="link"
                label={`${fixLabel(fix)}. ${saidAbout(listed.error, FIX_FALLBACK)} Opens Connectors`}
                onPress={() => router.push(adminRoutes.section("connectors") as never)}
                hitSlop={8}
                style={pill}
            >
                <Glyph name="branch" size={14} tone={color.ink3} />
                <Text size={13.5} tone={color.ink3} numberOfLines={1} style={{ flexShrink: 1 }}>{fixLabel(fix)}</Text>
            </Press>,
        );
    }

    const items = listed.data?.items ?? [];
    if (items.length === 0) return null;
    const owners = byOwner(items);
    const choose = (repository: string | null) => {
        setOpen(false);
        onPick(repository);
    };
    const option = (repository: string | null) => (
        <Press
            key={repository ?? "none"}
            role="radio"
            label={repository ?? NO_REPOSITORY}
            accessibilityState={{ checked: repository === picked }}
            onPress={() => choose(repository)}
            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 20 }}
        >
            <Text size={16} numberOfLines={1} style={{ flex: 1 }}>{repository ?? NO_REPOSITORY}</Text>
            {repository === picked ? <Icon name="check" size={18} width={2} tone={color.ink} /> : null}
        </Press>
    );
    return placed(
        <>
            <Press label={`${repositoryTitle(picked)}. Change`} onPress={() => setOpen(true)} hitSlop={8} style={pill}>
                <Glyph name="branch" size={14} tone={color.ink2} />
                <Text size={13.5} numberOfLines={1} style={{ flexShrink: 1 }}>{picked ?? NO_REPOSITORY}</Text>
                <Icon name="down" size={12} width={2} tone={color.ink2} />
            </Press>
            <Sheet open={open} onClose={() => setOpen(false)} title={repositoryTitle(null)}>
                <ScrollView>
                    {option(null)}
                    {owners.length > 1
                        ? owners.map(([owner, repos]) => (
                            <View key={owner} accessibilityRole="radiogroup" accessibilityLabel={owner}>
                                <Text size={12.5} tone={color.ink3} style={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: 2 }}>{owner}</Text>
                                {repos.map((repo) => option(repo.fullName))}
                            </View>
                        ))
                        : items.map((repo) => option(repo.fullName))}
                    {listed.data?.complete === false ? (
                        <Text size={12.5} tone={color.ink3} style={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 4 }}>{MORE_INSTALLED}</Text>
                    ) : null}
                </ScrollView>
            </Sheet>
        </>,
    );
}
