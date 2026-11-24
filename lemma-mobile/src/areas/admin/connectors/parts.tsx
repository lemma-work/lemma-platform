import { cloneElement, useMemo, useState, type ReactElement, type ReactNode } from "react";
import { ScrollView, TextInput, View } from "react-native";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { useSession } from "../../../session/session";
import { Icon } from "../../../ui/icons";
import { Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Counted, Num, Text } from "../../../ui/text";
import { color, font, TOUCH } from "../../../ui/tokens";
import { Row, Say } from "../ui";
import type { Connector } from "@web/data/accounts";
import type { OwnKind } from "@web/connect/install";
import { lookOf } from "./looks";
import { iconAddress, ownAppConnectors, searchConnectors, type Slice } from "./model";
import { connectorsRoutes } from "./routes";

/** The small pieces the connector screens share: a connector's mark, the
 *  search box, the three slices, and the sheet for adding one of the
 *  organization's own. */

/** A connector's logo, or its first letter when it has none that a phone
 *  can load (or the one it has will not load). */
export function Logo({ connector, size = 24 }: { connector: { title: string; icon: string }; size?: number }) {
    const { server } = useSession();
    const [broken, setBroken] = useState(false);
    const uri = iconAddress(connector.icon, server.web);
    if (uri && !broken) {
        return (
            <Image
                source={{ uri }}
                style={{ width: size, height: size, borderRadius: Math.round(size / 5) }}
                contentFit="contain"
                accessible={false}
                onError={() => setBroken(true)}
            />
        );
    }
    return (
        <View accessible={false} style={{ width: size, height: size, borderRadius: Math.round(size / 3.4), backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>
            <Text size={Math.round(size * 0.5)} tone={color.ink2}>{connector.title.slice(0, 1).toUpperCase()}</Text>
        </View>
    );
}

/** A search box that stays: the catalog is the thing being searched. */
export function SearchBox({ value, onChange, placeholder, label }: { value: string; onChange: (next: string) => void; placeholder: string; label: string }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, height: TOUCH, paddingLeft: 12, paddingRight: 4, borderRadius: 12, backgroundColor: color.wash }}>
            <Icon name="search" size={17} tone={color.ink3} />
            <TextInput
                accessibilityLabel={label}
                value={value}
                onChangeText={onChange}
                placeholder={placeholder}
                placeholderTextColor={color.ink3}
                autoCorrect={false}
                autoCapitalize="none"
                returnKeyType="search"
                style={{ flex: 1, fontFamily: font.regular, fontSize: 15.5, color: color.ink, paddingVertical: 0 }}
            />
            {value ? (
                <Press label="Clear the search" onPress={() => onChange("")} style={{ width: 36, height: 36, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="close" size={16} tone={color.ink2} />
                </Press>
            ) : null}
        </View>
    );
}

/** Connected, available, needs attention: one list with a stated filter,
 *  each saying how many it holds. The counts are the point: "69 available"
 *  says more about what is possible here than any six names would. */
export function Slices({ active, counts, onPick }: {
    active: Slice;
    counts: { connected: number; available: number; trouble: number };
    onPick: (slice: Slice) => void;
}) {
    const offered: { slice: Slice; label: string; n: number }[] = [
        { slice: "connected", label: "Connected", n: counts.connected },
        { slice: "available", label: "Available", n: counts.available },
        ...(counts.trouble > 0 ? [{ slice: "trouble" as const, label: "Needs attention", n: counts.trouble }] : []),
    ];
    return (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6 }} accessibilityRole="tablist">
            {offered.map((one) => {
                const on = one.slice === active;
                return (
                    <Press
                        key={one.slice}
                        role="tab"
                        label={`${one.label}, ${one.n}`}
                        accessibilityState={{ selected: on }}
                        onPress={() => onPick(one.slice)}
                        style={{ height: 36, paddingHorizontal: 14, borderRadius: 999, flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: on ? color.ink : "transparent", borderWidth: 1, borderColor: on ? color.ink : color.line2 }}
                    >
                        {one.slice === "trouble" ? <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: color.violet }} /> : null}
                        <Text size={14} tone={on ? color.onInk : color.ink}>{one.label}</Text>
                        <Num size={13.5} tone={on ? color.onInk : color.ink3}>{one.n}</Num>
                    </Press>
                );
            })}
        </ScrollView>
    );
}

/** Adding one of the organization's own, from the catalog: a server, an
 *  API or a database (each an install of the catalog entry that stands for
 *  every one of them), or an OAuth app of its own for a service that takes
 *  one, which first asks which service. */
export function AddOwnSheet({ open, onClose, kinds, connectors, ownApp }: {
    open: boolean;
    onClose: () => void;
    kinds: OwnKind[];
    connectors: readonly Connector[];
    ownApp: boolean;
}) {
    const router = useRouter();
    const [picking, setPicking] = useState(false);
    const [query, setQuery] = useState("");
    const eligible = useMemo(() => ownAppConnectors(connectors), [connectors]);
    const shown = searchConnectors(eligible, query);
    const close = () => { setPicking(false); setQuery(""); onClose(); };
    const go = (href: string) => { close(); router.push(href as never); };

    return (
        <Sheet open={open} onClose={close} title={picking ? "Your own OAuth app" : "Add your own"} tall={picking}>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 10 }} keyboardShouldPersistTaps="handled">
                {picking ? (
                    <>
                        <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>
                            Which service is the app for? You get the redirect URL to register with it, then paste its client ID and secret.
                        </Text>
                        {eligible.length > 6 ? <SearchBox value={query} onChange={setQuery} placeholder={`Search ${eligible.length} services`} label="Search services" /> : null}
                        <View>
                            {eligible.length === 0 ? <Text size={14.5} tone={color.ink2}>No connector here takes an app of your own.</Text> : null}
                            {eligible.length > 0 && shown.length === 0 ? <Text size={14.5} tone={color.ink2}>Nothing matches “{query.trim()}”.</Text> : null}
                            {shown.map((connector, index) => (
                                <Row
                                    key={connector.id}
                                    role="link"
                                    glyph={<Logo connector={connector} />}
                                    label={connector.title}
                                    hint={connector.description || null}
                                    last={index === shown.length - 1}
                                    onPress={() => go(connectorsRoutes.connect(connector.id, { own: true }))}
                                />
                            ))}
                        </View>
                    </>
                ) : (
                    <View>
                        {kinds.map((one, index) => {
                            const look = lookOf(one.kind);
                            return (
                                <Row
                                    key={one.kind}
                                    role="link"
                                    glyph={<KindGlyph kind={one.kind} />}
                                    label={look?.short ?? one.entry.title}
                                    hint={look?.tagline ?? "A connection you set up"}
                                    last={!ownApp && index === kinds.length - 1}
                                    onPress={() => go(connectorsRoutes.add(one.entry.id, one.kind))}
                                />
                            );
                        })}
                        {ownApp ? (
                            <Row role="button" glyph={<KindGlyph kind="oauth" />} label="OAuth app" hint="People sign in through yours, not Lemma’s" last onPress={() => setPicking(true)} />
                        ) : null}
                    </View>
                )}
            </ScrollView>
        </Sheet>
    );
}

/** A quiet mark for a kind of thing an organization adds itself. */
export function KindGlyph({ kind }: { kind: string }) {
    const name = kind === "sql" ? "table" : kind === "http" ? "file" : kind === "oauth" ? "people" : "automation";
    return <Icon name={name} size={18} tone={color.ink2} />;
}

/** A `Say` whose numbers are set in Inter: "Found 14 operations.",
 *  "3 accounts were connected to the old one". */
export function Note({ text, tone = "quiet" }: { text: string; tone?: "quiet" | "bad" | "done" }) {
    const ink = tone === "bad" ? color.amber : tone === "done" ? color.ink : color.ink2;
    return (
        <Say tone={tone}>
            <Counted size={14} tone={ink} style={{ lineHeight: 20 }}>{text}</Counted>
        </Say>
    );
}

/** A group's rows where some are there only sometimes: the last one shown
 *  draws no rule under it, whichever it is. */
export function Rows({ children }: { children: (ReactElement<{ last?: boolean }> | null | false)[] }) {
    const shown = children.filter((one): one is ReactElement<{ last?: boolean }> => Boolean(one));
    return <>{shown.map((row, index) => cloneElement(row, { key: row.key ?? index, last: index === shown.length - 1 }))}</>;
}

/** Buttons side by side, as the settings screens end their forms. */
export function Actions({ children }: { children: ReactNode }) {
    return <View style={{ flexDirection: "row", gap: 10, flexWrap: "wrap" }}>{children}</View>;
}
