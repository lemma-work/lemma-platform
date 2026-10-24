import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";
import { Image } from "expo-image";
import * as FileSystem from "expo-file-system/legacy";
import { Markdown } from "../../../ui/markdown";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color, font, TOUCH } from "../../../ui/tokens";
import { problemOf } from "../../admin/words";
import { FilesIcon, type FilesIconName } from "../../files/glyphs";
import { useFileBody, useFileStat, useSaveFile, type useFiles } from "./hooks";
import { countLine, entryNote, folderRows, saving, sizeLine, TOO_LARGE_TO_SAVE, viewerOf, whereSay } from "./model";
import { crumbs, type Entry, type Roots } from "@web/computer/machine";

/** The computer's files (the web's file drawer, `computer-view.tsx`): the
 *  trail from "Computer", what the folder is for, folders first, build
 *  output and dotfiles held back, and a file opened where it stands. */

type Listing = ReturnType<typeof useFiles>;

export function FilesPlace({ listing, path, roots, open, hidden, onGo, onOpen, onShowHidden, onWake }: {
    listing: Listing;
    /** Where the listing is, in the server's own words. */
    path: string;
    roots: Roots;
    open: Entry | null;
    hidden: boolean;
    onGo: (path: string) => void;
    onOpen: (entry: Entry | null) => void;
    onShowHidden: (show: boolean) => void;
    onWake: () => void;
}) {
    const first = listing.data?.pages[0];
    const all = listing.data?.pages.flatMap((page) => page.entries ?? []) ?? [];
    const rows = folderRows(all, hidden);
    const noise = hidden ? folderRows(all, false).hidden : rows.hidden;
    const where = whereSay(path, roots);

    return (
        <View style={{ gap: 4 }}>
            <Trail path={path} roots={roots} open={open} onGo={onGo} onClose={() => onOpen(null)} />
            {open ? <OpenFile key={open.path} entry={open} /> : (
                <>
                    {where ? <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19, paddingBottom: 4 }}>{where}</Text> : null}
                    {listing.isPending || listing.isPlaceholderData ? <ActivityIndicator color={color.ink3} style={{ paddingVertical: 18, alignSelf: "flex-start" }} />
                        : listing.isError ? <Trouble said={problemOf(listing.error, "This computer couldn’t be read just now.")} onRetry={() => void listing.refetch()} />
                        : first?.sleeping ? (
                            <View style={{ gap: 12, paddingVertical: 8 }}>
                                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>
                                    This computer is asleep. It starts when work needs it, and stops again once it has been idle a while. Wake it to see its files.
                                </Text>
                                <View style={{ flexDirection: "row" }}><Button title="Wake it" onPress={onWake} /></View>
                            </View>
                        ) : first?.exists === false ? (
                            <Quiet>Nothing has been written here yet. A chat’s folder is made the first time something is saved into it.</Quiet>
                        ) : rows.shown.length === 0 ? (
                            <Quiet>{rows.hidden > 0 ? "Only build output and dotfiles here." : "This folder is empty."}</Quiet>
                        ) : (
                            <View>
                                {rows.shown.map((entry, index) => (
                                    <EntryRow key={entry.path} entry={entry} last={index === rows.shown.length - 1} onPress={() => (entry.kind === "directory" ? onGo(entry.path) : onOpen(entry))} />
                                ))}
                            </View>
                        )}
                    {first && !first.sleeping && !listing.isPlaceholderData && (rows.shown.length > 0 || rows.hidden > 0) ? (
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: 40, borderTopWidth: 1, borderTopColor: color.line }}>
                            <Num size={13} tone={color.ink3} style={{ flex: 1 }}>{countLine(rows.shown.length, rows.hidden)}</Num>
                            {rows.hidden > 0 && !hidden ? <Quick label="Show them" onPress={() => onShowHidden(true)} /> : null}
                            {hidden && noise > 0 ? <Quick label="Hide them" onPress={() => onShowHidden(false)} /> : null}
                            {listing.hasNextPage ? (
                                listing.isFetchingNextPage ? <ActivityIndicator size="small" color={color.ink3} /> : <Quick label="Load more" onPress={() => void listing.fetchNextPage()} />
                            ) : null}
                        </View>
                    ) : null}
                </>
            )}
        </View>
    );
}

/** The path as steps from "Computer", one row that scrolls sideways and
 *  keeps its end in view. With a file open, its folder is a step back. */
function Trail({ path, roots, open, onGo, onClose }: { path: string; roots: Roots; open: Entry | null; onGo: (path: string) => void; onClose: () => void }) {
    const steps = crumbs(path, roots);
    const scroller = useRef<ScrollView>(null);
    useEffect(() => {
        const timer = setTimeout(() => scroller.current?.scrollToEnd({ animated: false }), 0);
        return () => clearTimeout(timer);
    }, [path, open]);
    return (
        <ScrollView ref={scroller} horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -20 }} contentContainerStyle={{ paddingHorizontal: 20, alignItems: "center" }}>
            {steps.map((step, index) => {
                const here = index === steps.length - 1 && !open;
                return (
                    <View key={step.path} style={{ flexDirection: "row", alignItems: "center" }}>
                        {index > 0 ? <Icon name="forward" size={13} tone={color.ink3} /> : null}
                        <Press
                            role="link"
                            label={here ? `${step.name}, here` : `Go to ${step.name}`}
                            disabled={here}
                            onPress={() => (index === steps.length - 1 ? onClose() : onGo(step.path))}
                            style={{ minHeight: 36, justifyContent: "center", paddingHorizontal: 4 }}
                        >
                            <Text size={13.5} tone={here ? color.ink : color.ink2} weight={here ? "medium" : "regular"}>{step.name}</Text>
                        </Press>
                    </View>
                );
            })}
            {open ? (
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                    <Icon name="forward" size={13} tone={color.ink3} />
                    <Text size={13.5} weight="medium" style={{ paddingHorizontal: 4 }}>{open.name}</Text>
                </View>
            ) : null}
        </ScrollView>
    );
}

function glyphOf(entry: Entry): FilesIconName {
    if (entry.kind === "directory") return "folder";
    const viewer = viewerOf(entry.name);
    return viewer === "image" ? "image" : viewer === "markdown" ? "page" : "file";
}

/** One file or folder on one line: what it is, its name, and its size or
 *  the way in. */
function EntryRow({ entry, last, onPress }: { entry: Entry; last: boolean; onPress: () => void }) {
    const folder = entry.kind === "directory";
    return (
        <Press
            role="link"
            label={folder ? `${entry.name}, folder` : `${entry.name}, ${entryNote(entry)}`}
            onPress={onPress}
            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 4, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}
        >
            <View style={{ width: 22, alignItems: "center" }}>
                <FilesIcon name={glyphOf(entry)} size={19} tone={folder ? color.ink : color.ink2} />
            </View>
            <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>{entry.name}</Text>
            {folder ? <Icon name="forward" size={16} tone={color.ink3} /> : <Num size={13} tone={color.ink3}>{entryNote(entry)}</Num>}
        </Press>
    );
}

/** A file, open where it stands: its text, its picture, or what it is and
 *  a way to save it. Its bytes are read when it opens and let go when it
 *  closes. */
function OpenFile({ entry }: { entry: Entry }) {
    const body = useFileBody(entry.path, entry.name);
    const stat = useFileStat(entry.path);
    /* The stat is the file now; the listing's line is what it was. */
    const size = stat.data?.size_bytes ?? entry.size_bytes;
    const viewer = viewerOf(entry.name);
    const picture = body.data?.kind === "image" ? body.data.uri : null;
    /* A picture is a file in the cache: let go of it with the file. */
    useEffect(() => () => {
        if (picture) void FileSystem.deleteAsync(picture, { idempotent: true }).catch(() => undefined);
    }, [picture]);

    return (
        <View style={{ gap: 12, paddingTop: 4 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 36 }}>
                <Num size={13} tone={color.ink3} style={{ flex: 1 }}>{sizeLine(size)}</Num>
                <Save path={entry.path} sizeBytes={size} />
            </View>
            {body.isPending ? <ActivityIndicator color={color.ink3} style={{ paddingVertical: 18, alignSelf: "flex-start" }} />
                : body.isError ? <Trouble said={problemOf(body.error, "Couldn’t read this file. Try again in a moment.")} onRetry={() => void body.refetch()} />
                : body.data.kind === "text" ? (viewer === "markdown" ? <Markdown text={body.data.text} size={15} /> : <Plain text={body.data.text} />)
                : body.data.kind === "image" ? <Picture uri={body.data.uri} name={entry.name} />
                : body.data.kind === "too-large" ? <Quiet>{`${sizeLine(size)}, more than a phone should show here. Save it, or ask in the chat what is in it.`}</Quiet>
                : <Quiet>A phone doesn’t show this kind of file here. Save it to open it in another app.</Quiet>}
        </View>
    );
}

/** Text as it is, in Inter at a small size; long lines scroll sideways. */
function Plain({ text }: { text: string }) {
    if (!text) return <Quiet>This file is empty.</Quiet>;
    return (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -20 }} contentContainerStyle={{ paddingHorizontal: 20 }}>
            <Text selectable size={12.5} style={{ fontFamily: font.num, lineHeight: 19 }}>{text}</Text>
        </ScrollView>
    );
}

function Picture({ uri, name }: { uri: string; name: string }) {
    const [ratio, setRatio] = useState(4 / 3);
    const [failed, setFailed] = useState(false);
    if (failed) return <Quiet>This picture couldn’t be drawn. Save it to open it in another app.</Quiet>;
    return (
        <Image
            source={{ uri }}
            accessibilityLabel={name}
            contentFit="contain"
            onLoad={(event) => {
                const { width, height } = event.source;
                if (width > 0 && height > 0) setRatio(width / height);
            }}
            onError={() => setFailed(true)}
            style={{ width: "100%", aspectRatio: Math.max(ratio, 0.5), borderRadius: 10, backgroundColor: color.wash }}
        />
    );
}

/** Save: the whole file into the phone's cache, then the share sheet. */
function Save({ path, sizeBytes }: { path: string; sizeBytes: number | null }) {
    const save = useSaveFile();
    const tooLarge = saving(sizeBytes) === "too-large";
    const [told, setTold] = useState(false);
    if (save.isPending) return <ActivityIndicator size="small" color={color.ink3} />;
    return (
        <View style={{ alignItems: "flex-end", maxWidth: "70%" }}>
            <Press
                label="Save"
                onPress={() => (tooLarge ? setTold(true) : save.mutate({ path, sizeBytes }))}
                style={{ flexDirection: "row", alignItems: "center", gap: 6, minHeight: TOUCH, paddingLeft: 8 }}
            >
                <FilesIcon name="save" size={16} tone={color.ink} />
                <Text size={14} weight="medium">{save.isError ? "Try saving again" : "Save"}</Text>
            </Press>
            {tooLarge && told ? <Text size={12.5} tone={color.amber} style={{ textAlign: "right" }}>{TOO_LARGE_TO_SAVE}</Text> : null}
            {save.isError ? <Text size={12.5} tone={color.amber} style={{ textAlign: "right" }}>{problemOf(save.error, "Couldn’t save this file.")}</Text> : null}
        </View>
    );
}

function Quick({ label, onPress }: { label: string; onPress: () => void }) {
    return (
        <Press label={label} onPress={onPress} style={{ minHeight: 40, justifyContent: "center" }}>
            <Text size={13.5} tone={color.ink2}>{label}</Text>
        </Press>
    );
}

export function Quiet({ children }: { children: string }) {
    return <Text size={15} tone={color.ink2} style={{ lineHeight: 21, paddingVertical: 8 }}>{children}</Text>;
}

export function Trouble({ said, onRetry }: { said: string; onRetry?: () => void }) {
    return (
        <View style={{ gap: 4, paddingVertical: 6 }}>
            <Text accessibilityRole="alert" size={14.5} tone={color.amber} style={{ lineHeight: 20 }}>{said}</Text>
            {onRetry ? (
                <Press label="Try again" onPress={onRetry} style={{ alignSelf: "flex-start", minHeight: 36, justifyContent: "center" }}>
                    <Text size={14.5} weight="medium">Try again</Text>
                </Press>
            ) : null}
        </View>
    );
}
