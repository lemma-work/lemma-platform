import { useEffect, useState, type ReactNode } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from "react-native";
import { stampOf } from "../../data/model";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Num, Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { FilesIcon, type FilesIconName } from "./glyphs";
import { useFolder } from "./hooks";
import { canMoveInto, crumbs, detailOf, glyphOf, isInternal, nameOf, ordered, titleOf } from "./model";
import type { LibraryItem } from "./source";
import { fileReading } from "@web/library/file-status";
import { fileLocations } from "@web/library/file-locations";

/** The pieces the files screens are made of: a row, the sheets a row and
 *  the + open, and the line a write's problem is said on. */

/** One file or folder, on one line: what it is, its name, and at the end
 *  either what is happening to it or when it last changed. */
export function FileRow({ item, now, onPress, onLongPress, busy = false }: {
    item: LibraryItem;
    now: number;
    onPress: () => void;
    onLongPress?: () => void;
    busy?: boolean;
}) {
    const reading = item.kind === "file" ? fileReading(item.status) : null;
    const title = titleOf(item);
    const at = Date.parse(item.updated);
    return (
        <Press
            role="link"
            label={title + (reading ? ", " + reading.label : "")}
            accessibilityHint={onLongPress ? "Hold for more" : undefined}
            onPress={onPress}
            onLongPress={onLongPress}
            delayLongPress={350}
            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 4, paddingHorizontal: 4 }}
        >
            <View style={{ width: 22, alignItems: "center" }}>
                <FilesIcon name={glyphOf(item)} size={19} tone={item.kind === "folder" ? color.ink : color.ink2} />
            </View>
            <View style={{ flex: 1, minWidth: 0 }}>
                <Text size={15.5} numberOfLines={1}>{title}</Text>
            </View>
            {busy ? <ActivityIndicator size="small" color={color.ink3} />
                : reading ? <Text size={13} tone={reading.state === "failed" ? color.amber : color.ink3}>{reading.label}</Text>
                : Number.isFinite(at) ? <Num size={13} tone={color.ink3}>{stampOf(at, now)}</Num> : null}
            {item.kind === "folder" ? <Icon name="forward" size={16} tone={color.ink3} /> : null}
        </Press>
    );
}

/** A label over a group of rows, with an action at its end. */
export function GroupLabel({ children, action }: { children: string; action?: { label: string; onPress: () => void } }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", marginTop: 14, marginBottom: 2, paddingHorizontal: 4, minHeight: 28 }}>
            <Text size={12.5} tone={color.ink3} style={{ flex: 1 }}>{children}</Text>
            {action ? (
                <Press label={action.label} onPress={action.onPress} style={{ minHeight: 32, justifyContent: "center", paddingLeft: 12 }}>
                    <Text size={13.5} tone={color.ink2}>{action.label}</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** One action in a sheet. */
export function SheetRow({ icon, label, detail, onPress, disabled = false }: { icon: FilesIconName; label: string; detail?: string; onPress: () => void; disabled?: boolean }) {
    return (
        <Press label={label} onPress={onPress} disabled={disabled} style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: TOUCH + 6, paddingHorizontal: 20, opacity: disabled ? 0.4 : 1 }}>
            <FilesIcon name={icon} size={20} tone={color.ink} />
            <View style={{ flex: 1, minWidth: 0 }}>
                <Text size={16}>{label}</Text>
                {detail ? <Text size={13} tone={color.ink3} numberOfLines={2}>{detail}</Text> : null}
            </View>
        </Press>
    );
}

/** A write that did not happen, said where it was asked for. */
export function WriteProblem({ problem, onDismiss }: { problem: string | null; onDismiss: () => void }) {
    if (!problem) return null;
    return (
        <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 10, marginVertical: 8, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 12, backgroundColor: color.amberWash }}>
            <Text size={14} tone={color.amber} style={{ flex: 1 }}>{problem}</Text>
            <Press label="Dismiss" onPress={onDismiss} style={{ padding: 2 }}>
                <Icon name="close" size={16} tone={color.amber} />
            </Press>
        </View>
    );
}

/** A name to type: a new folder, a rename. */
export function NameSheet({ open, title, initial, action, onClose, onSubmit, busy = false }: {
    open: boolean;
    title: string;
    initial: string;
    action: string;
    onClose: () => void;
    onSubmit: (name: string) => void;
    busy?: boolean;
}) {
    const [name, setName] = useState(initial);
    useEffect(() => { if (open) setName(initial); }, [open, initial]);
    /* A rename starts with the name selected up to its extension, so typing
       replaces the name and keeps the kind. */
    const dot = initial.lastIndexOf(".");
    const [selection, setSelection] = useState<{ start: number; end: number } | undefined>(undefined);
    useEffect(() => { if (open) setSelection({ start: 0, end: dot > 0 ? dot : initial.length }); }, [open, initial, dot]);
    return (
        <Sheet open={open} onClose={onClose} title={title}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <View style={{ paddingHorizontal: 20, paddingTop: 4, gap: 14 }}>
                    <TextInput
                        accessibilityLabel={title}
                        value={name}
                        onChangeText={(next) => { setName(next); setSelection(undefined); }}
                        selection={selection}
                        onSelectionChange={() => setSelection(undefined)}
                        autoFocus
                        autoCorrect={false}
                        returnKeyType="done"
                        onSubmitEditing={() => name.trim() && onSubmit(name)}
                        placeholder="Name"
                        placeholderTextColor={color.ink3}
                        style={{ fontFamily: font.regular, fontSize: 17, color: color.ink, borderBottomWidth: 1, borderBottomColor: color.line2, paddingVertical: 10 }}
                    />
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        <Button title="Cancel" kind="outline" grow onPress={onClose} />
                        <Button title={action} grow busy={busy} disabled={!name.trim()} onPress={() => onSubmit(name)} />
                    </View>
                </View>
            </KeyboardAvoidingView>
        </Sheet>
    );
}

/** Ask before removing something: name what goes, and what that means. */
export function ConfirmDelete({ item, onClose, onConfirm }: { item: LibraryItem | null; onClose: () => void; onConfirm: () => void }) {
    return (
        <Sheet open={item !== null} onClose={onClose}>
            {item ? (
                <View style={{ paddingHorizontal: 20, gap: 16, paddingTop: 4 }}>
                    <Text size={17} style={{ lineHeight: 24 }}>
                        Delete {titleOf(item)}{item.kind === "folder" ? " and everything in it" : ""}? It is gone for everyone.
                    </Text>
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        <Button title="Keep it" kind="outline" grow onPress={onClose} />
                        <Button title="Delete" grow onPress={onConfirm} />
                    </View>
                </View>
            ) : <View />}
        </Sheet>
    );
}

/** Where to move something: the folders of its location, opened one at a
 *  time, and "Move here" for the one shown. */
export function MoveSheet({ podId, item, onClose, onMove }: { podId: string; item: LibraryItem | null; onClose: () => void; onMove: (folder: string) => void }) {
    const root = item && /^\/me(\/|$)/.test(item.path) ? fileLocations.personal.root : fileLocations.shared.root;
    const [at, setAt] = useState(root);
    useEffect(() => { if (item) setAt(root); }, [item, root]);
    const folder = useFolder(podId, at, item !== null);
    const folders = ordered((folder.data?.pages ?? []).flatMap((page) => page.items))
        .filter((one) => one.kind === "folder" && !isInternal(one) && !(root === "/" && one.path === "/me"));
    const here = at === root ? (root === "/" ? "Shared files" : "My files") : nameOf(at);
    const allowed = item ? canMoveInto(item, at) : false;
    return (
        <Sheet open={item !== null} onClose={onClose} title={item ? `Move ${titleOf(item)}` : undefined} tall>
            <View style={{ paddingHorizontal: 12 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, minHeight: TOUCH, paddingHorizontal: 8 }}>
                    {at !== root ? (
                        <Press label="Up a folder" onPress={() => setAt(at.slice(0, at.lastIndexOf("/")) || root)} style={{ width: 36, height: 36, alignItems: "center", justifyContent: "center", marginLeft: -8 }}>
                            <Icon name="back" size={20} />
                        </Press>
                    ) : null}
                    <Text size={16} weight="medium" numberOfLines={1} style={{ flex: 1 }}>{here}</Text>
                </View>
                <ScrollView style={{ maxHeight: 360 }} keyboardShouldPersistTaps="handled">
                    {folder.isPending ? <ActivityIndicator color={color.ink3} style={{ padding: 16 }} /> : null}
                    {!folder.isPending && folders.length === 0 ? <Text size={14.5} tone={color.ink3} style={{ padding: 8 }}>No folders in here.</Text> : null}
                    {folders.map((one) => (
                        <Press key={one.path} role="link" label={`Open ${one.name}`} disabled={item?.path === one.path} onPress={() => setAt(one.path)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH, paddingHorizontal: 8, opacity: item?.path === one.path ? 0.4 : 1 }}>
                            <FilesIcon name="folder" size={19} tone={color.ink} />
                            <Text size={15.5} numberOfLines={1} style={{ flex: 1 }}>{one.name}</Text>
                            <Icon name="forward" size={16} tone={color.ink3} />
                        </Press>
                    ))}
                </ScrollView>
                <View style={{ paddingHorizontal: 8, paddingTop: 12 }}>
                    <Button title={allowed ? `Move to ${here}` : "It is already here"} disabled={!allowed} onPress={() => onMove(at)} />
                </View>
            </View>
        </Sheet>
    );
}

/** The things a row can have done to it, from a long press. */
export function ItemSheet({ item, onClose, actions }: {
    item: LibraryItem | null;
    onClose: () => void;
    actions: { icon: FilesIconName; label: string; detail?: string; run: () => void; hidden?: boolean }[];
}) {
    return (
        <Sheet open={item !== null} onClose={onClose}>
            {item ? (
                <View>
                    <View style={{ paddingHorizontal: 20, paddingBottom: 8, gap: 2 }}>
                        <Text size={17} weight="medium" numberOfLines={2}>{titleOf(item)}</Text>
                        <Text size={13} tone={color.ink3} numberOfLines={1}>{detailOf(item)}</Text>
                    </View>
                    {actions.filter((one) => !one.hidden).map((one) => (
                        <SheetRow key={one.label} icon={one.icon} label={one.label} detail={one.detail} onPress={() => { onClose(); one.run(); }} />
                    ))}
                </View>
            ) : <View />}
        </Sheet>
    );
}

/** The trail from a location's root to a folder, each step a way back. */
export function Trail({ path, root, onGo }: { path: string; root: string; onGo: (path: string) => void }) {
    const steps = crumbs(path, root);
    const rootName = root === "/" ? "Shared" : "Mine";
    const parts: ReactNode[] = [];
    parts.push(
        <Press key="root" role="link" label={`Back to ${rootName}`} onPress={() => onGo(root)} style={{ paddingVertical: 4 }}>
            <Text size={13} tone={color.ink3}>{rootName}</Text>
        </Press>,
    );
    steps.slice(0, -1).forEach((step) => {
        parts.push(<Text key={step.path + "/"} size={13} tone={color.ink3}> / </Text>);
        parts.push(
            <Press key={step.path} role="link" label={`Back to ${step.name}`} onPress={() => onGo(step.path)} style={{ paddingVertical: 4 }}>
                <Text size={13} tone={color.ink3} numberOfLines={1}>{step.name}</Text>
            </Press>,
        );
    });
    return <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap" }}>{parts}</View>;
}
