import { useState } from "react";
import { ScrollView, View } from "react-native";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import { Loading } from "../../ui/chrome";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { Label, TextAction, Trouble } from "./bits";
import { chatKeys, useChannel, useChatsSource, useGrantableColumns, useGrantableTables, useTableGrants } from "./hooks";
import { isForbidden } from "./model";
import { problemOf } from "@web/channels/problem";
import { GIVE_REFUSED, grantSaid, mayGive, toggled } from "@web/channels/table-grants-model";

/** What a group's people outside the Lemma may do with its tables (the
 *  web's `channels/table-grants.tsx`): read the columns given, and add a row
 *  with the columns given. Given one table at a time by the group's owners
 *  or an admin, through their own access, and everybody in the group reads
 *  what is given, so the section says so. Only tables the reader reads
 *  whole are offered; the server decides again. */
export function TableGrants({ podId, channelId, lemmaName, answerer }: { podId: string; channelId: string; lemmaName: string; answerer: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    /* The group's own rule, its owners or whoever manages the Lemma's chats,
       which the server asks too. */
    const may = useChannel(podId, channelId).data?.me.canManage === true;
    const grants = useTableGrants(podId, channelId);
    const [giving, setGiving] = useState(false);
    const reread = () => void cache.invalidateQueries({ queryKey: chatKeys.grants(key, podId, channelId) });
    const remove = useMutation({ mutationFn: (table: string) => source.removeTableGrant(podId, channelId, table), onSuccess: reread });
    const given = grants.data ?? [];
    return (
        <View style={{ gap: 2 }}>
            <Label>{`What they can do with ${lemmaName}’s tables`}</Label>
            {grants.isPending ? <Loading label="Reading its tables" /> : grants.isError ? (
                <Trouble>{problemOf(grants.error, "Couldn’t read what it was given.")}</Trouble>
            ) : given.length === 0 ? (
                <Text size={14} tone={color.ink2} style={{ lineHeight: 20, paddingHorizontal: 4 }}>{`Nothing yet: they read only what is Public, and ${answerer} writes nothing down for them.`}</Text>
            ) : given.map((grant) => (
                <View key={grant.table} style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 40, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
                    <Text size={14.5} numberOfLines={1} style={{ flex: 1 }}>
                        {grant.table}
                        <Text size={13} tone={color.ink3}>{"  " + grantSaid(grant)}</Text>
                    </Text>
                    {may ? <TextAction label="Take back" tone={color.amber} disabled={remove.isPending} onPress={() => remove.mutate(grant.table)} /> : null}
                </View>
            ))}
            {may ? <TextAction label="Give them a table" tone={color.ink} onPress={() => setGiving(true)} /> : null}
            {given.length > 0 ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18, paddingHorizontal: 4 }}>{`Everyone in this group reads what you give it, people outside ${lemmaName} included.`}</Text> : null}
            <Trouble>{remove.isError ? problemOf(remove.error, "Couldn’t take it back.") : null}</Trouble>
            <Sheet open={giving} onClose={() => setGiving(false)} title="Give them a table" tall>
                {giving ? <GiveTable podId={podId} channelId={channelId} given={given.map((one) => one.table)} onDone={() => { setGiving(false); reread(); }} /> : null}
            </Sheet>
        </View>
    );
}

/** A table, then its columns: each one read, added with, both or neither. */
function GiveTable({ podId, channelId, given, onDone }: { podId: string; channelId: string; given: readonly string[]; onDone: () => void }) {
    const source = useChatsSource();
    const [table, setTable] = useState("");
    const [read, setRead] = useState<Set<string>>(new Set());
    const [add, setAdd] = useState<Set<string>>(new Set());
    const tables = useGrantableTables(podId, true);
    const columns = useGrantableColumns(podId, table);
    const save = useMutation({
        mutationFn: () => source.setTableGrant(podId, channelId, table, { readColumns: [...read], addColumns: [...add] }),
        onSuccess: onDone,
    });
    const pick = (name: string) => { setTable(name); setRead(new Set()); setAdd(new Set()); save.reset(); };
    return (
        <>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8 }} keyboardShouldPersistTaps="handled">
                {!table ? (
                    tables.isPending ? <Loading label="Reading the tables" /> : tables.isError ? (
                        <Trouble>{problemOf(tables.error, "Couldn’t read the tables.")}</Trouble>
                    ) : (tables.data ?? []).length === 0 ? (
                        <Text size={14.5} tone={color.ink2} style={{ paddingVertical: 12 }}>There is no table here you could give.</Text>
                    ) : (tables.data ?? []).map((name, index, all) => (
                        <Press key={name} label={name} onPress={() => pick(name)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, borderBottomWidth: index === all.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                            <Text size={16} numberOfLines={1} style={{ flex: 1 }}>{name}</Text>
                            {given.includes(name) ? <Text size={13} tone={color.ink3}>Given</Text> : null}
                            <Icon name="forward" size={16} tone={color.ink3} />
                        </Press>
                    ))
                ) : (
                    <View>
                        <Press label="Pick another table" onPress={() => pick("")} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, borderBottomWidth: 1, borderBottomColor: color.line }}>
                            <Text size={16} numberOfLines={1} style={{ flex: 1 }}>{table}</Text>
                            <Text size={14} tone={color.ink3}>Change</Text>
                        </Press>
                        <View style={{ flexDirection: "row", alignItems: "center", minHeight: 30 }}>
                            <Text size={12.5} tone={color.ink3} style={{ flex: 1 }}>Column</Text>
                            <Text size={12.5} tone={color.ink3} style={{ width: 132, textAlign: "center" }}>They can</Text>
                        </View>
                        {columns.isPending ? <Loading label="Reading its columns" /> : columns.isError ? (
                            <Trouble>{problemOf(columns.error, "Couldn’t read its columns.")}</Trouble>
                        ) : (columns.data ?? []).map((name, index, all) => (
                            <View key={name} style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 48, borderBottomWidth: index === all.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                                <Text size={15.5} numberOfLines={1} style={{ flex: 1 }}>{name}</Text>
                                <Tick label="Read" column={name} on={read.has(name)} onPress={() => setRead((was) => toggled(was, name))} />
                                <Tick label="Add" column={name} on={add.has(name)} onPress={() => setAdd((was) => toggled(was, name))} />
                            </View>
                        ))}
                    </View>
                )}
                <View style={{ paddingTop: 8 }}>
                    <Trouble>{save.isError ? (isForbidden(save.error) ? GIVE_REFUSED : problemOf(save.error, "Couldn’t give it that table.")) : null}</Trouble>
                </View>
            </ScrollView>
            <View style={{ flexDirection: "row", gap: 10, paddingHorizontal: 20, paddingTop: 8 }}>
                <Button title="Cancel" kind="outline" grow height={46} onPress={onDone} />
                <Button title={save.isPending ? "Giving…" : "Give it"} grow height={46} busy={save.isPending} disabled={!mayGive(table, read, add)} onPress={() => save.mutate()} />
            </View>
        </>
    );
}

/** One of a column's two ticks, as a chip: filled when on. */
function Tick({ label, column, on, onPress }: { label: string; column: string; on: boolean; onPress: () => void }) {
    return (
        <Press accessibilityRole="checkbox" label={`${label} ${column}`} accessibilityState={{ checked: on }} onPress={onPress} style={{ width: 62, height: 34, borderRadius: 999, alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 4, backgroundColor: on ? color.ink : "transparent", borderWidth: on ? 0 : 1, borderColor: color.line2 }}>
            {on ? <Icon name="check" size={13} width={2} tone={color.onInk} /> : null}
            <Text size={13.5} tone={on ? color.onInk : color.ink}>{label}</Text>
        </Press>
    );
}
