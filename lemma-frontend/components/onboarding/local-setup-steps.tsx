"use client";

/**
 * The two questions only a local installation has to answer.
 *
 * Hosted Lemma has models of its own and is reachable by definition. A Lemma
 * Desktop install has neither settled until someone settles it, and both were
 * previously buried in a separate settings webview that onboarding never
 * mentioned — which is how a user could finish setup and find agents did not
 * work.
 *
 * Everything here is answered in place. An earlier pass split this into a
 * provider step and an agents step and sent the provider half to Local settings
 * to be completed, which is the correct security boundary and the wrong
 * product: onboarding cannot ask "which model?" and then open a different
 * window for the answer. The boundary moved instead — `configure_ai_provider`
 * reaches one narrow daemon command that merges the AI section and cannot touch
 * sharing, tunnels, or the runtime — so the question and its answer live
 * together.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useThisComputer } from "@/lib/desktop/this-computer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
    ArrowRight,
    Check,
    Globe2,
    Home,
    Plus,
    RefreshCw,
    Share2,
    Sparkles,
    TerminalSquare,
} from "@/components/ui/icons";
import { HarnessProfileDialog, type HarnessDialogTarget } from "@/components/agents/harness-profile-dialog";
import { selectWorkspaceTarget } from "@/components/agents/this-computer-status";
import { useAutoConnectThisComputer } from "@/lib/desktop/auto-connect";
import { getLemmaApiBaseUrl } from "@/lib/sdk/lemma-client";
import {
    configureAiProvider,
    useDesktopBridge,
    discoverProviderModels,
    openLocalSettings,
    useLocalAiStatus,
    type AiProfileDraft,
} from "@/lib/desktop/local-capabilities";
import {
    useAgentHostHarnesses,
    useAgentHosts,
    useManagedAgentRuntimes,
    useRestoreAgentRuntime,
} from "@/lib/hooks/use-agent-runtime";
import {
    isArchivedProfile,
    isDiscoveringHarnesses,
} from "@/components/agents/agent-runtime-helpers";
import { HarnessRow } from "@/components/agents/harness-row";
import { StepLoader } from "@/components/brand/loader";
import { Skeleton } from "@/components/shared/loading";
import {
    RECHECK_SETTLE_MS,
    discoveryHeadline,
    discoveryLines,
    discoveryPhase,
    discoveryStatusLine,
    harnessRowStates,
} from "@/components/agents/harness-discovery-rows";
import { agentHostBridge } from "@/lib/desktop/agent-host-bridge";
import { RuntimeProfileKind } from "lemma-sdk";
import { SetupPrimaryButton, SetupSplitPanel } from "./account-onboarding-chrome";
import type { SetupStep } from "./account-onboarding-helpers";

type StepChrome = {
    onBack?: () => void;
    steps?: SetupStep[];
};

/**
 * Shared right-hand panel.
 *
 * `justify-center` rather than filling: the left column centres its content
 * between the progress bar and the bottom, so a preview pinned to the top read
 * as misaligned against it.
 */
function LocalPreview({
    icon,
    headline,
    lines,
    working = false,
}: {
    icon: React.ReactNode;
    headline: string;
    lines: string[];
    /** Whether these lines describe work still in progress. */
    working?: boolean;
}) {
    return (
        <div className="flex h-full w-full flex-col justify-center gap-4 text-left">
            <span className="flex size-10 items-center justify-center rounded-md bg-[var(--surface-2)] text-[var(--text-secondary)]">
                {icon}
            </span>
            <p className="text-lg font-medium tracking-[-0.018em] text-[var(--text-primary)]">{headline}</p>
            <ul className="space-y-2">
                {lines.map((line) => (
                    <li key={line} className="flex gap-2 text-sm text-[var(--text-tertiary)]">
                        {working ? (
                            /* A green tick on "Each agent is started once to see
                               what it offers" said the starting was done. It is
                               the thing being waited for. */
                            <StepLoader size="xs" className="mt-0.5 shrink-0" />
                        ) : (
                            <Check className="mt-0.5 size-3.5 shrink-0 text-[var(--state-success)]" />
                        )}
                        <span>{line}</span>
                    </li>
                ))}
            </ul>
        </div>
    );
}

/**
 * When the bridge is missing, every control here is dead. That happens for a
 * LAN or public-link visitor, whose origin is deliberately absent from the
 * desktop capability.
 */
/**
 * A known agent this computer has not reported yet, or has reported nothing for.
 *
 * Its whole job is to make waiting legible: the row is there from the first
 * frame with the agent's name on it, shimmering while the host looks, and
 * settles into "Not installed" only once the scan is genuinely over. Before
 * this, an unresolved list was a single line of prose in an otherwise empty
 * panel, sitting next to a preview that named three agents as if they were
 * already there.
 */
function SkeletonHarnessRow({ displayName, looking }: { displayName: string; looking: boolean }) {
    return (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-3">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)]">
                <TerminalSquare className="size-3.5 text-[var(--text-tertiary)]" />
            </span>
            <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-tertiary)]">{displayName}</span>
            {looking ? (
                /* A placeholder, not a liveness pulse: there is no content here
                   yet. The design system keeps those two apart deliberately. */
                <Skeleton shape="text" className="h-4 w-16 shrink-0 rounded-full" />
            ) : (
                <span className="shrink-0 text-xs text-[var(--text-tertiary)]">Not installed</span>
            )}
        </div>
    );
}

function BridgeUnavailableNote() {
    return (
        <p className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-2 text-xs text-[var(--text-tertiary)]">
            This has to be done on the computer running Lemma, in the desktop app.
        </p>
    );
}

// ---------------------------------------------------------------------------
// What answers in chats
// ---------------------------------------------------------------------------

type Preset = {
    id: string;
    title: string;
    hint: string;
    protocol: AiProfileDraft["protocol"];
    baseUrl: string;
    needsKey: boolean;
};

// Local runners first: someone running Lemma on their own Mac most likely has
// one of these serving already, and neither needs a key or an account.
//
// `hint` takes the noun rather than reading it: evaluated at module scope it is
// whatever the *first* evaluation saw, which on the server is "this computer"
// for the life of the process -- frozen, and different from what the client
// would render.
const presets = (computer: string): Preset[] => [
    { id: "ollama", title: "Ollama", hint: `Runs on ${computer}`, protocol: "openai_compat", baseUrl: "http://127.0.0.1:11434/v1", needsKey: false },
    { id: "lmstudio", title: "LM Studio", hint: `Runs on ${computer}`, protocol: "openai_compat", baseUrl: "http://127.0.0.1:1234/v1", needsKey: false },
    { id: "openai", title: "OpenAI", hint: "API key", protocol: "openai_compat", baseUrl: "https://api.openai.com/v1", needsKey: true },
    { id: "anthropic", title: "Anthropic", hint: "API key", protocol: "anthropic_compat", baseUrl: "https://api.anthropic.com", needsKey: true },
    { id: "openrouter", title: "OpenRouter", hint: "API key", protocol: "openai_compat", baseUrl: "https://openrouter.ai/api/v1", needsKey: true },
];

/**
 * Milliseconds since the flag went true, ticking while it stays true.
 *
 * Only reason it exists: the copy has to change as a wait goes on, and nothing
 * else on this screen knows how long anything has taken. Stops when the work
 * does, so a settled screen is not re-rendering once a second for a number
 * nobody reads.
 */
function useElapsedWhile(active: boolean): number {
    // State written only from the interval and read straight back, so nothing
    // sets state while an effect runs and nothing reads a clock during render --
    // the two ways this turns into a cascade of renders.
    const [elapsedMs, setElapsedMs] = useState(0);

    useEffect(() => {
        if (!active) return;
        const began = Date.now();
        const timer = setInterval(() => setElapsedMs(Date.now() - began), 1000);
        return () => {
            clearInterval(timer);
            setElapsedMs(0);
        };
    }, [active]);

    return active ? elapsedMs : 0;
}

export function LocalIntelligenceStep({
    organizationId,
    onContinue,
    onBack,
    steps,
}: StepChrome & {
    organizationId: string | null;
    onContinue: (outcome: "ready" | "deferred") => void;
}) {
    const hasBridge = useDesktopBridge();
    // A hook, so the server render and the first client render agree. Reading
    // `thisComputer()` directly answers differently in the two, which is a
    // hydration mismatch on every string below.
    const computerNoun = useThisComputer();
    // Connects itself. Nobody is asked to press anything for a machine that is
    // already this workspace's own computer.
    const { status } = useAutoConnectThisComputer();
    // This workspace's pairing, not the first one on the machine. `targets[0]`
    // is whichever pairing happens to sort first, so a Mac already paired to
    // another workspace showed that host's agents on this one's setup screen.
    const hostId = selectWorkspaceTarget(status?.targets ?? [], getLemmaApiBaseUrl())?.host_id ?? null;
    const harnesses = useAgentHostHarnesses(hostId);
    // A host publishes nothing until it has found its first agent, so an
    // empty list right after pairing means "still probing", not "none
    // installed". Nothing on the wire distinguishes them, so it is inferred
    // from how long this computer has been paired.
    const hosts = useAgentHosts();
    const host = hosts.data?.items?.find((candidate) => candidate.id === hostId);
    // Archived profiles included on purpose. They are out of the catalog but
    // still hold their name, so a harness whose profile was archived looks
    // unadded here and offering "Use in chats" walks straight into
    // "Runtime profile named 'Claude Code' already exists".
    const managed = useManagedAgentRuntimes(organizationId, { includeArchived: true });
    const restore = useRestoreAgentRuntime();
    const { status: aiStatus } = useLocalAiStatus(true);

    const [preset, setPreset] = useState<Preset | null>(null);
    const [apiKey, setApiKey] = useState("");
    const [models, setModels] = useState<string[]>([]);
    const [model, setModel] = useState("");
    const [listing, setListing] = useState(false);
    const [applying, setApplying] = useState(false);
    const [dialog, setDialog] = useState<HarnessDialogTarget | null>(null);

    const detected = useMemo(() => harnesses.data?.items ?? [], [harnesses.data?.items]);
    // One reading of "where are we", used by both halves of this screen. They
    // used to answer separately: the left column reported a scan in progress
    // while the right promised three agents by name, which is two screens'
    // worth of confidence in one.
    const phase = discoveryPhase({
        hostAvailable: status?.available,
        paired: status?.paired,
        // `isLoading`, not `isFetching`. This screen polls every two seconds, and
        // `isFetching` is true for each of those — so a settled list threw itself
        // back to four shimmering placeholders, twice a second, forever. The
        // screen never looked like it had finished because it kept saying it had
        // not.
        fetching: harnesses.isLoading,
        stillDiscovering: !!host && isDiscoveringHarnesses(host, detected.length),
    });
    const rows = useMemo(() => harnessRowStates(detected, phase), [detected, phase]);
    const foundCount = rows.filter((row) => row.state === "found").length;
    const working = phase !== "settled" && phase !== "unavailable";
    // A clock, only while there is something to time. Probing spawns every agent
    // on the machine, and how long that takes is the one thing the screen knows
    // and the user does not -- but a wait is only worth explaining once it has
    // gone on long enough to be worth explaining.
    const elapsedMs = useElapsedWhile(working);
    const statusLine = discoveryStatusLine({
        phase,
        foundCount,
        elapsedMs,
        computer: computerNoun,
    });
    // Asking the host to look again, which is a different act from asking the
    // server what it was told last time. The host reads the request off its
    // control file on a five-second beat and then probes every agent, so the
    // answer arrives over the next few seconds through the poll this screen
    // already runs -- there is no moment to refetch *at*.
    const [rechecking, setRechecking] = useState(false);
    const recheck = useCallback(() => {
        setRechecking(true);
        void agentHostBridge.refresh().then(
            () => {
                toast.success(`Rechecking the agents on ${computerNoun}`);
                // Long enough to cover the control-file beat and a probe, so the
                // button stays busy until there is something new to look at
                // rather than for a guessed 1.2s that expired before the host
                // had even read the request.
                setTimeout(() => setRechecking(false), RECHECK_SETTLE_MS);
            },
            (error: unknown) => {
                setRechecking(false);
                toast.error(error instanceof Error ? error.message : String(error));
            },
        );
    }, [computerNoun]);
    // Which harnesses already have a profile, so a row can say so instead of
    // offering to add the same agent again. Mutations invalidate the whole
    // agent-runtime tree, so this updates itself the moment one is saved.
    const savedByHarnessId = useMemo(() => {
        const saved = new Map<string, { id: string; name: string; archived: boolean }>();
        for (const profile of managed.data?.items ?? []) {
            if (profile.kind === RuntimeProfileKind.HARNESS && profile.harness_id) {
                saved.set(profile.harness_id, {
                    id: profile.id,
                    name: profile.name,
                    archived: isArchivedProfile(profile),
                });
            }
        }
        return saved;
    }, [managed.data?.items]);

    const hasAgent = [...savedByHarnessId.values()].some((saved) => !saved.archived);
    const configured = hasAgent || aiStatus === "ready";

    const draft = (): AiProfileDraft | null =>
        preset
            ? {
                  protocol: preset.protocol,
                  base_url: preset.baseUrl,
                  default_model: model,
                  models,
                  vision_models: [],
                  allow_private_network: false,
              }
            : null;

    const listModels = async () => {
        const candidate = draft();
        if (!candidate) return;
        setListing(true);
        try {
            const found = await discoverProviderModels({ ...candidate, default_model: "", models: [] }, apiKey);
            if (!found.length) {
                toast.error("That provider answered, but reported no models.");
                return;
            }
            setModels(found);
            setModel((current) => (found.includes(current) ? current : found[0]));
        } catch (error) {
            toast.error(error instanceof Error ? error.message : String(error));
        } finally {
            setListing(false);
        }
    };

    const apply = async () => {
        const candidate = draft();
        if (!candidate || !model) return;
        setApplying(true);
        try {
            await configureAiProvider(candidate, apiKey);
            toast.success(`${preset?.title} is ready.`);
        } catch (error) {
            toast.error(error instanceof Error ? error.message : String(error));
        } finally {
            setApplying(false);
        }
    };

    return (
        <SetupSplitPanel
            title="What should answer in your chats?"
            subtitle={`A coding agent already on ${computerNoun}, an API provider, or both. Nothing here has AI until one of them is set.`}
            preview={
                <LocalPreview
                    icon={<Sparkles className="size-5" />}
                    headline={discoveryHeadline(phase, foundCount, computerNoun)}
                    lines={discoveryLines(phase, foundCount, computerNoun)}
                    working={working}
                />
            }
            onBack={onBack}
            currentStep="intelligence"
            steps={steps}
        >
            {/* max-w-xl, matching the title's measure above it — the panel sets
                that on the heading but leaves children full width, so a wider
                block here sits visibly proud of the text it belongs to. */}
            <div className="w-full max-w-xl space-y-5 text-left">
                {!hasBridge ? <BridgeUnavailableNote /> : null}

                <section className="space-y-2">
                    <div className="flex items-center justify-between">
                        <p className="text-xs font-medium text-[var(--text-tertiary)]">
                            Agents on this computer
                        </p>
                        <Button
                            type="button"
                            variant="quiet"
                            size="sm"
                            className="gap-1.5 px-2"
                            disabled={rechecking || !hostId}
                            onClick={recheck}
                        >
                            <RefreshCw className={rechecking ? "size-3.5 lemma-spin" : "size-3.5"} />
                            Rescan
                        </Button>
                    </div>

                    {/*
                     * Here rather than only in the preview, which is `hidden
                     * lg:flex` -- so on a narrow window the screen said nothing
                     * at all for the whole minute a first probe takes, next to
                     * four grey rows and a disabled button.
                     */}
                    {statusLine ? (
                        <p
                            role="status"
                            aria-live="polite"
                            className="flex items-center gap-2 px-1 text-xs text-[var(--text-tertiary)]"
                        >
                            <StepLoader size="xs" className="shrink-0" />
                            {statusLine}
                        </p>
                    ) : null}

                    {/*
                     * Every agent Lemma can drive, from the first frame, each
                     * resolving on its own. The previous version showed an empty
                     * panel with one sentence in it for the whole minute a first
                     * probe takes — which reads as broken rather than busy — and
                     * then had the list appear out of nothing.
                     */}
                    {rows.map((row) => {
                        if (row.state !== "found") {
                            return (
                                <SkeletonHarnessRow
                                    key={row.key}
                                    displayName={row.displayName}
                                    looking={row.state === "looking"}
                                />
                            );
                        }
                        const saved = savedByHarnessId.get(row.harness.id);
                        return (
                            <HarnessRow
                                key={row.harness.id}
                                harness={row.harness}
                                savedProfile={saved ? { name: saved.name, archived: saved.archived } : null}
                                onRecheck={recheck}
                                className="border border-[var(--border-subtle)]"
                                action={(usable) => {
                                    if (!organizationId) return null;
                                    if (saved?.archived) {
                                        // Restoring rather than adding: the
                                        // archived profile still owns the name,
                                        // so a second "Use in chats" would only
                                        // ever 409 on it.
                                        return (
                                            <Button
                                                type="button"
                                                variant="quiet"
                                                size="sm"
                                                className="gap-1.5 px-2"
                                                onClick={() => {
                                                    void restore
                                                        .mutateAsync({ organizationId, profileId: saved.id })
                                                        .then(() => {
                                                            toast.success(`${saved.name} restored`);
                                                            void managed.refetch();
                                                        })
                                                        .catch((error: unknown) =>
                                                            toast.error(
                                                                error instanceof Error
                                                                    ? error.message
                                                                    : String(error),
                                                            ),
                                                        );
                                                }}
                                            >
                                                <RefreshCw className="size-3.5" />
                                                Restore {saved.name}
                                            </Button>
                                        );
                                    }
                                    // Withheld for a harness that cannot take
                                    // the profile — a signed-out agent used to
                                    // offer this, walk the user through the
                                    // dialog, and fail on save.
                                    if (saved || !usable) return null;
                                    return (
                                        <Button
                                            type="button"
                                            variant="quiet"
                                            size="sm"
                                            className="gap-1.5 px-2"
                                            onClick={() => setDialog({ mode: "create", harness: row.harness })}
                                        >
                                            <Plus className="size-3.5" />
                                            Use in chats
                                        </Button>
                                    );
                                }}
                            />
                        );
                    })}

                    {phase === "settled" && foundCount === 0 ? (
                        <p className="px-1 text-xs text-[var(--text-tertiary)]">
                            macOS may ask for file access the first time an agent is probed — allow it
                            and press Rescan.
                        </p>
                    ) : null}
                </section>

                <section className="space-y-3">
                    <p className="text-xs font-medium text-[var(--text-tertiary)]">
                        Or connect a model provider
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {presets(computerNoun).map((candidate) => (
                            <button
                                key={candidate.id}
                                type="button"
                                data-active={preset?.id === candidate.id}
                                onClick={() => {
                                    setPreset(candidate);
                                    // The models belonged to the last endpoint.
                                    setModels([]);
                                    setModel("");
                                    setApiKey("");
                                }}
                                className={[
                                    "setup-path-choice flex min-w-[7.5rem] flex-col gap-0.5 px-3 py-2 text-left",
                                    preset?.id === candidate.id ? "is-active" : "",
                                ].join(" ")}
                            >
                                <span className="text-sm font-medium text-[var(--text-primary)]">{candidate.title}</span>
                                <span className="text-xs text-[var(--text-tertiary)]">{candidate.hint}</span>
                            </button>
                        ))}
                    </div>

                    {preset ? (
                        <div className="space-y-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-4 py-4">
                            <p className="text-xs text-[var(--text-tertiary)]">{preset.baseUrl}</p>
                            {preset.needsKey ? (
                                <Input
                                    type="password"
                                    autoComplete="new-password"
                                    value={apiKey}
                                    onChange={(event) => setApiKey(event.target.value)}
                                    placeholder="API key"
                                    aria-label={`${preset.title} API key`}
                                />
                            ) : null}

                            {models.length ? (
                                <div className="space-y-1.5">
                                    <span className="block text-xs font-medium text-[var(--text-tertiary)]">
                                        Default model
                                    </span>
                                    <Select value={model} onValueChange={setModel}>
                                        <SelectTrigger aria-label="Default model">
                                            <SelectValue placeholder="Pick a model" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {models.map((name) => (
                                                <SelectItem key={name} value={name}>
                                                    {name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            ) : null}

                            <div className="flex flex-wrap gap-2">
                                <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    loading={listing}
                                    loadingLabel="Connecting"
                                    disabled={!hasBridge || (preset.needsKey && !apiKey.trim())}
                                    onClick={() => void listModels()}
                                >
                                    {models.length ? "List models again" : "Connect and list models"}
                                </Button>
                                {models.length ? (
                                    <Button
                                        type="button"
                                        size="sm"
                                        loading={applying}
                                        loadingLabel="Applying — Lemma restarts"
                                        disabled={!model}
                                        onClick={() => void apply()}
                                    >
                                        Use {model}
                                    </Button>
                                ) : null}
                            </div>
                        </div>
                    ) : null}
                </section>

                <p className="text-xs text-[var(--text-tertiary)]">
                    A provider is this installation&apos;s single default — one profile, not one per
                    person. If you later open Lemma to your network or the web, that key answers for
                    everyone. A coding agent stays on {computerNoun} and uses its own credentials.
                </p>

                {/*
                  * `pt-9` and `!mt-0` together, because `SetupPrimaryButton`
                  * carries `mx-auto mt-8` of its own. This row already cancelled
                  * the centring; leaving the top margin meant `items-center`
                  * centred the button's *margin* box while the "Ready" pip
                  * centred on the line, so the pip sat visibly above the middle
                  * of the button. The 2rem moves to the container, where it
                  * applies to the whole row.
                  */}
                <div className="flex flex-wrap items-center gap-3 pt-9">
                    <SetupPrimaryButton
                        type="button"
                        onClick={() => onContinue(configured ? "ready" : "deferred")}
                        className="!mx-0 !mt-0"
                    >
                        Continue
                        <ArrowRight className="h-4 w-4" />
                    </SetupPrimaryButton>
                    {configured ? (
                        <span className="flex items-center gap-1.5 text-xs text-[var(--state-success)]">
                            <Check className="size-3.5" />
                            Ready
                        </span>
                    ) : null}
                </div>
            </div>

            {organizationId ? (
                <HarnessProfileDialog
                    target={dialog}
                    organizationId={organizationId}
                    onClose={() => setDialog(null)}
                    onSaved={() => {
                        // The row's "Added as …" comes from the managed listing,
                        // which the mutation already invalidates; the harness
                        // list is refetched because its health can change too.
                        void managed.refetch();
                        void harnesses.refetch();
                    }}
                />
            ) : null}
        </SetupSplitPanel>
    );
}

// ---------------------------------------------------------------------------
// Who can reach this installation
// ---------------------------------------------------------------------------

type AccessMode = "this_computer" | "local_network" | "public";

const ACCESS_MODES: Array<{
    id: AccessMode;
    title: string;
    subtitle: string;
    icon: React.ReactNode;
}> = [
    {
        id: "this_computer",
        title: "Just this computer",
        subtitle: "Nothing is exposed. You can change this whenever you want.",
        icon: <Home className="size-4" />,
    },
    {
        id: "local_network",
        title: "This Wi-Fi network",
        subtitle: "Use Lemma from your phone or another machine on the same network.",
        icon: <Share2 className="size-4" />,
    },
    {
        id: "public",
        title: "A public link",
        subtitle: "Needs ngrok or Cloudflare. Anyone with the URL can create an account here.",
        icon: <Globe2 className="size-4" />,
    },
];

export function LocalSharingStep({
    onContinue,
    onBack,
    steps,
}: StepChrome & { onContinue: () => void }) {
    const hasBridge = useDesktopBridge();
    const [selected, setSelected] = useState<AccessMode>("this_computer");

    const handleContinue = async () => {
        if (selected === "this_computer") {
            // Already the default. Nothing to activate, nothing to confirm.
            onContinue();
            return;
        }
        // Turning exposure on restarts both services, needs an interface choice
        // for LAN and a fresh confirmation for public. Those stay in Local
        // settings: unlike the model, they are not a question onboarding can
        // finish, and they are the writes the workspace deliberately cannot make.
        if (!(await openLocalSettings("sharing"))) {
            toast.error("Sharing can only be turned on in the Lemma desktop app.");
            return;
        }
        onContinue();
    };

    return (
        <SetupSplitPanel
            title="Who can reach this Lemma?"
            subtitle="It runs on this computer. You decide whether anything else can talk to it."
            preview={
                <LocalPreview
                    icon={<Share2 className="size-5" />}
                    headline="Private by default"
                    lines={[
                        "Nothing is exposed until you say so.",
                        "Network and public access never resume on their own.",
                        "Turn either off again from Local settings at any time.",
                    ]}
                />
            }
            onBack={onBack}
            currentStep="sharing"
            steps={steps}
        >
            <div className="w-full max-w-xl space-y-3 text-left">
                {ACCESS_MODES.map((mode) => (
                    <button
                        key={mode.id}
                        type="button"
                        onClick={() => setSelected(mode.id)}
                        aria-pressed={selected === mode.id}
                        data-active={selected === mode.id}
                        // The onboarding choice-card primitive, the same one the
                        // audience and start steps use. A local step is still an
                        // onboarding step; it does not get its own card.
                        className={[
                            "setup-path-choice flex w-full items-start gap-3 px-4 py-4 text-left",
                            selected === mode.id ? "is-active" : "",
                        ].join(" ")}
                    >
                        <span
                            className={[
                                "setup-path-choice-icon flex h-9 w-9 shrink-0 items-center justify-center",
                                selected === mode.id ? "is-active" : "",
                            ].join(" ")}
                        >
                            {mode.icon}
                        </span>
                        <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                                {mode.title}
                                {selected === mode.id ? <Check className="h-4 w-4" /> : null}
                            </span>
                            <span className="mt-1 block text-sm text-[var(--text-tertiary)]">
                                {mode.subtitle}
                            </span>
                        </span>
                    </button>
                ))}

                {selected !== "this_computer" ? (
                    <p className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-2.5 text-xs text-[var(--text-secondary)]">
                        Anyone who connects uses the provider you configured a step ago — its usage
                        and its bill are yours. Continuing opens Local settings to finish turning
                        this on.
                    </p>
                ) : null}

                {hasBridge || selected === "this_computer" ? null : <BridgeUnavailableNote />}

                <div className="pt-1">
                    <SetupPrimaryButton
                        type="button"
                        onClick={() => void handleContinue()}
                        disabled={selected !== "this_computer" && !hasBridge}
                        className="!mx-0"
                    >
                        Continue
                        <ArrowRight className="h-4 w-4" />
                    </SetupPrimaryButton>
                </div>
            </div>
        </SetupSplitPanel>
    );
}
