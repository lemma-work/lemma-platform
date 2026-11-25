import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";
import { Image } from "expo-image";
import { useFocusEffect, useRouter } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { SafeAreaView } from "react-native-safe-area-context";
import { useSession } from "../../../session/session";
import { BackLink } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { Markdown } from "../../../ui/markdown";
import { Button, Press } from "../../../ui/press";
import { Counted, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { Glyph } from "../../../thread/glyphs";
import { chatRoutes } from "../../chat/routes";
import { Field, Group, ReadProblem, Reading, Say, SettingsScreen, problemOf } from "../../admin/ui";
import { moreRoutes } from "../routes";
import { useJourney, useReadme, useRememberInstall, useRemix } from "./hooks";
import { JourneySheet } from "./journey";
import {
    NEW, PUBLIC_TEMPLATES, coverUrl, footFor, githubHref, isRepositoryName, isWorking, phoneWords, remixName, remixSourceOf, remixWords,
    shownOf, templateOf, templateRoute, type PublicTemplate,
} from "./model";

/* ── Templates ───────────────────────────────────────────────────────── */

const COVER = { width: 72, height: 40 };

/** A template's cover, small and rounded, on a quiet ground that stands in
 *  while it loads, when it fails, and in sample data (which never reaches
 *  the network). */
function Cover({ template }: { template: PublicTemplate }) {
    const { mode, server } = useSession();
    const [broken, setBroken] = useState(false);
    return (
        <View style={{ ...COVER, borderRadius: 8, backgroundColor: color.wash, overflow: "hidden" }}>
            {mode === "live" && !broken ? (
                <Image source={{ uri: coverUrl(server.web, template) }} style={COVER} contentFit="cover" transition={150} accessible={false} onError={() => setBroken(true)} />
            ) : null}
        </View>
    );
}

/** Working examples to start from (the web's `/templates`): one dense row
 *  each, opening its install page. */
export function TemplatesScreen() {
    const router = useRouter();
    return (
        <SettingsScreen title="Templates" note="Working examples you can install and make your own.">
            <View>
                {PUBLIC_TEMPLATES.map((template, index) => (
                    <Press
                        key={template.slug}
                        role="link"
                        label={`${template.name}, ${template.category}. ${template.description}`}
                        onPress={() => router.push(templateRoute(template) as never)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 62, paddingVertical: 10, borderBottomWidth: index === PUBLIC_TEMPLATES.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                    >
                        <Cover template={template} />
                        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                            <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
                                <Text size={16} numberOfLines={1} style={{ flexShrink: 1 }}>{template.name}</Text>
                                <Text size={12.5} tone={color.ink3} numberOfLines={1} style={{ marginLeft: "auto" }}>{template.category}</Text>
                            </View>
                            <Text size={13.5} tone={color.ink3} numberOfLines={1}>{phoneWords(template.description)}</Text>
                        </View>
                    </Press>
                ))}
            </View>
        </SettingsScreen>
    );
}

/* ── Install ─────────────────────────────────────────────────────────── */

/** A screen of its own, with its way back. */
function Page({ children, foot }: { children: ReactNode; foot?: ReactNode }) {
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                <BackLink />
            </View>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 28, gap: 26 }}>{children}</ScrollView>
            {foot}
        </SafeAreaView>
    );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
    return (
        <View style={{ gap: 8 }}>
            <Text accessibilityRole="header" size={12.5} tone={color.ink3}>{label}</Text>
            {children}
        </View>
    );
}

/** A link that leaves the app for the browser. */
function Outward({ label, href, size = 14.5 }: { label: string; href: string; size?: number }) {
    return (
        <Press role="link" label={label} onPress={() => void WebBrowser.openBrowserAsync(href)} style={{ alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 5, minHeight: TOUCH - 8 }}>
            <Text size={size} weight="medium">{label}</Text>
            <Glyph name="external" size={13} tone={color.ink2} />
        </Press>
    );
}

/** A repository on GitHub, and installing it on a Lemma (the web's
 *  `/import/github/{owner}/{repo}`): what it is, what it does and brings
 *  when it is one of the catalogue's, its README, and a fixed Install at the
 *  foot that opens the journey in a tall sheet. Any public repository
 *  works, not only the catalogue's. */
export function InstallScreen({ owner, repo }: { owner: string; repo: string }) {
    const valid = isRepositoryName(owner) && isRepositoryName(repo);
    if (!valid) {
        return (
            <Page>
                <Text size={24} weight="medium">Nothing to install here</Text>
                <Say>This link does not name a GitHub repository. An install link names its owner and its repository, as in github.com/owner/repository.</Say>
            </Page>
        );
    }
    /* Keyed by the repository: a link to another one, opened while this
       screen is on top, reuses the screen with new params, and its journey
       must start again rather than carry the last one's. */
    return <Install key={owner + "/" + repo} owner={owner} repo={repo} />;
}

function Install({ owner, repo }: { owner: string; repo: string }) {
    const router = useRouter();
    const template = templateOf(owner, repo);
    const readme = useReadme(owner, repo);
    /* Read once per README, not on every poll of the journey. */
    const read = readme.isPending ? undefined : readme.data ?? null;
    const shown = useMemo(() => shownOf(owner, repo, template, read), [owner, repo, template, read]);
    const title = shown.title;
    const journey = useJourney({ owner, repo, title });
    const [open, setOpen] = useState(false);
    /* Left for Settings to connect an account: open the journey again on
       coming back. */
    const reopen = useRef(false);
    useFocusEffect(useCallback(() => {
        if (reopen.current) {
            reopen.current = false;
            setOpen(true);
        }
    }, []));
    /* Kept on the phone once the name has settled, so the app can offer the
       install back if the way here is lost. */
    useRememberInstall(readme.isPending ? null : { path: moreRoutes.install(owner, repo), title });

    const leave = useCallback((href: string, back: boolean) => {
        reopen.current = back;
        setOpen(false);
        router.push(href as never);
    }, [router]);

    const github = githubHref(owner, repo);
    const foot = footFor(journey.job, title, journey.who);
    const waiting = journey.job?.status === "AWAITING_CONFIRMATION";
    return (
        <Page
            foot={
                <>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 20, paddingTop: 10, paddingBottom: 6, borderTopWidth: 1, borderTopColor: color.line, backgroundColor: color.paper }}>
                        {isWorking(journey.job) ? <ActivityIndicator size="small" color={color.violet} /> : null}
                        {waiting ? <View accessibilityLabel="Needs you" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} /> : null}
                        {journey.job?.status === "COMPLETED" && journey.chosenLemma ? <LemmaFace id={journey.chosenLemma.id} icon={journey.chosenLemma.iconUrl} size={24} /> : null}
                        <View style={{ flex: 1, minWidth: 0 }}>
                            <Counted size={15} numberOfLines={1}>{foot.line}</Counted>
                        </View>
                        <Button title={foot.action} onPress={() => setOpen(true)} />
                    </View>
                    {/* Outside the page's scroll, so it takes no place in it. */}
                    <JourneySheet open={open} onClose={() => setOpen(false)} journey={journey} title={title} leave={leave} />
                </>
            }
        >
            <View style={{ gap: 8 }}>
                <Outward label={`${owner}/${repo}`} href={github} size={13.5} />
                <Text accessibilityRole="header" size={28} weight="medium" style={{ letterSpacing: -0.5, lineHeight: 33 }}>{title}</Text>
                {shown.lede ? <Text size={16} tone={color.ink2} style={{ lineHeight: 23 }}>{phoneWords(shown.lede)}</Text> : null}
            </View>

            {template ? (
                <>
                    <Section label="What it does">
                        <View>
                            {template.outcomes.map((outcome, index) => (
                                <View key={outcome} style={{ flexDirection: "row", gap: 10, paddingVertical: 10, borderBottomWidth: index === template.outcomes.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                                    <View style={{ paddingTop: 2 }}><Icon name="check" size={15} width={2} tone={color.ink3} /></View>
                                    <Text size={15.5} style={{ flex: 1, lineHeight: 21 }}>{phoneWords(outcome)}</Text>
                                </View>
                            ))}
                        </View>
                    </Section>
                    <Section label="What comes with it">
                        <View>
                            {template.includes.map((part, index) => (
                                <View key={part.label} style={{ paddingVertical: 10, gap: 2, borderBottomWidth: index === template.includes.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                                    <Text size={15.5} weight="medium">{part.label}</Text>
                                    <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{phoneWords(part.detail)}</Text>
                                </View>
                            ))}
                        </View>
                    </Section>
                </>
            ) : null}

            <Section label="From the README">
                {readme.isPending ? (
                    <Reading label="Reading the README" />
                ) : shown.body !== null ? (
                    <View style={{ gap: 10 }}>
                        <Markdown text={shown.body} size={15} />
                        {shown.cut ? <Outward label="Read the rest on GitHub" href={github} /> : null}
                    </View>
                ) : (
                    <View style={{ gap: 4 }}>
                        <Say>The README could not be read. You can still install it, and review what it adds first.</Say>
                        <Outward label="Read it on GitHub" href={github} />
                    </View>
                )}
            </Section>
        </Page>
    );
}

/* ── Remix ───────────────────────────────────────────────────────────── */

/** Bring an app to a Lemma (the web's `/remix?source=`): the app's address,
 *  the organization when there are several, a new Lemma or one of the
 *  organization's, and a new chat in it with the words in the box, unsent. */
export function RemixScreen({ source }: { source: string | null }) {
    const parsed = remixSourceOf(source);
    const title = "Make it yours";
    const note = "Bring an app to a Lemma and build on it together.";
    if (parsed.kind === "missing") {
        return (
            <SettingsScreen title={title} note={note}>
                <Say tone="bad">This link names no app to remix. Open Remix from the app you want to bring here.</Say>
            </SettingsScreen>
        );
    }
    if (parsed.kind === "bad") {
        return (
            <SettingsScreen title={title} note={note}>
                <Say tone="bad">{`“${parsed.said}” is not an app’s address. A remix needs one that starts with http or https.`}</Say>
            </SettingsScreen>
        );
    }
    return <Remix href={parsed.href} title={title} note={note} />;
}

function Remix({ href, title, note }: { href: string; title: string; note: string }) {
    const router = useRouter();
    const remix = useRemix();
    const [typed, setTyped] = useState<string | null>(null);
    const [orgsOpen, setOrgsOpen] = useState(false);
    const name = remix.created?.name ?? typed ?? remixName(href);
    const isNew = remix.target === NEW;
    const { orgs } = remix;

    const go = async () => {
        const podId = await remix.prepare(name);
        if (podId) router.push(chatRoutes.filled(podId, remixWords(href)) as never);
    };

    return (
        <SettingsScreen title={title} note={note}>
            <Group label="The app">
                <Text size={15} selectable numberOfLines={3} style={{ paddingVertical: 10, lineHeight: 21 }}>{href}</Text>
            </Group>

            {orgs.pending ? <Reading label="Reading your organizations" /> : orgs.error && !orgs.orgs.length ? (
                <ReadProblem said={problemOf(orgs.error, "Your organizations could not be read.")} onRetry={orgs.reread} />
            ) : !orgs.orgs.length ? (
                <View style={{ gap: 10 }}>
                    <Say>You are not in an organization yet. Make one, then open this link again.</Say>
                    <Button title="Make an organization" kind="outline" onPress={() => router.push(moreRoutes.newOrg as never)} />
                </View>
            ) : (
                <>
                    {orgs.orgs.length > 1 ? (
                        <Group label="Organization">
                            <Pick label={orgs.org?.name ?? ""} hint={orgsOpen ? "Done" : "Change"} onPress={() => setOrgsOpen((was) => !was)} last={!orgsOpen} />
                            {orgsOpen ? orgs.orgs.map((one, index) => (
                                <Pick key={one.id} on={one.id === orgs.org?.id} label={one.name} onPress={() => { remix.chooseOrg(one.id); setOrgsOpen(false); }} last={index === orgs.orgs.length - 1} />
                            )) : null}
                        </Group>
                    ) : null}

                    <Group label="Which Lemma">
                        <Pick on={isNew} label="A new Lemma" onPress={() => remix.setTarget(NEW)} last={isNew || !remix.list.length} />
                        {isNew ? (
                            <View style={{ paddingTop: 4, paddingBottom: 12, borderBottomWidth: remix.list.length ? 1 : 0, borderBottomColor: color.line }}>
                                <Field label="Name" value={name} onChange={setTyped} editable={!remix.created} maxLength={60} autoCapitalize="words" hint={remix.created ? "Made already: going on adds to it." : null} />
                            </View>
                        ) : null}
                        {remix.lemmas.isPending ? <Reading label="Reading your Lemmas" /> : null}
                        {remix.lemmas.isError ? <ReadProblem said={problemOf(remix.lemmas.error, "Your Lemmas could not be read.")} onRetry={() => void remix.lemmas.refetch()} /> : null}
                        {remix.list.map((lemma, index) => (
                            <Pick key={lemma.id} on={remix.target === lemma.id} label={lemma.name} glyph={<LemmaFace id={lemma.id} icon={lemma.iconUrl} size={28} />} onPress={() => remix.setTarget(lemma.id)} last={index === remix.list.length - 1} />
                        ))}
                    </Group>

                    <View style={{ gap: 10 }}>
                        <Button title="Continue" busy={remix.busy} disabled={!remix.orgId || (isNew && !name.trim())} onPress={() => void go()} />
                        <Say>The Lemma picks it up in a new chat, where you can steer the remix.</Say>
                        {remix.error ? <Say tone="bad">{remix.error}</Say> : null}
                    </View>
                </>
            )}
        </SettingsScreen>
    );
}

/** A row chosen in place: a tick on the one held, or a word at its end. */
function Pick({ on = false, label, hint, glyph, onPress, last = false }: { on?: boolean; label: string; hint?: string; glyph?: ReactNode; onPress: () => void; last?: boolean }) {
    return (
        <Press
            role={hint ? "button" : "radio"}
            label={label}
            accessibilityState={hint ? undefined : { selected: on }}
            onPress={onPress}
            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, paddingVertical: 6, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}
        >
            {glyph ? <View style={{ width: 30, alignItems: "center" }}>{glyph}</View> : null}
            <Text size={16} numberOfLines={1} style={{ flex: 1 }}>{label}</Text>
            {hint ? <Text size={14.5} tone={color.ink3}>{hint}</Text> : on ? <Icon name="check" size={18} width={2} /> : null}
        </Press>
    );
}
