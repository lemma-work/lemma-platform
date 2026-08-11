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

import { useMemo, useState } from "react";
import { toast } from "sonner";
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
import { useAutoConnectThisComputer } from "@/lib/desktop/auto-connect";
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
}: {
    icon: React.ReactNode;
    headline: string;
    lines: string[];
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
                        <Check className="mt-0.5 size-3.5 shrink-0 text-[var(--state-success)]" />
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
const PRESETS: Preset[] = [
    { id: "ollama", title: "Ollama", hint: "Runs on this Mac", protocol: "openai_compat", baseUrl: "http://127.0.0.1:11434/v1", needsKey: false },
    { id: "lmstudio", title: "LM Studio", hint: "Runs on this Mac", protocol: "openai_compat", baseUrl: "http://127.0.0.1:1234/v1", needsKey: false },
    { id: "openai", title: "OpenAI", hint: "API key", protocol: "openai_compat", baseUrl: "https://api.openai.com/v1", needsKey: true },
    { id: "anthropic", title: "Anthropic", hint: "API key", protocol: "anthropic_compat", baseUrl: "https://api.anthropic.com", needsKey: true },
    { id: "openrouter", title: "OpenRouter", hint: "API key", protocol: "openai_compat", baseUrl: "https://openrouter.ai/api/v1", needsKey: true },
];

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
    // Connects itself. Nobody is asked to press anything for a machine that is
    // already this workspace's own computer.
    const status = useAutoConnectThisComputer();
    const hostId = status?.targets[0]?.host_id ?? null;
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

    const detected = harnesses.data?.items ?? [];
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
            subtitle="A coding agent already on this Mac, an API provider, or both. Nothing here has AI until one of them is set."
            preview={
                <LocalPreview
                    icon={<Sparkles className="size-5" />}
                    headline="Two ways to get a working agent"
                    lines={[
                        "Claude Code, Codex, or OpenCode — no key, no model id.",
                        "Or Ollama, LM Studio, OpenAI, Anthropic, OpenRouter.",
                        "Lemma lists what a provider offers; you pick the default.",
                    ]}
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
                            disabled={harnesses.isFetching || !hostId}
                            onClick={() => void harnesses.refetch()}
                        >
                            <RefreshCw className={harnesses.isFetching ? "size-3.5 lemma-spin" : "size-3.5"} />
                            Rescan
                        </Button>
                    </div>

                    {detected.map((harness) => {
                        const saved = savedByHarnessId.get(harness.id);
                        return (
                            <div
                                key={harness.id}
                                className="flex flex-wrap items-center gap-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2.5"
                            >
                                <TerminalSquare className="size-4 shrink-0 text-[var(--text-tertiary)]" />
                                <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                                    {harness.display_name}
                                </span>
                                {saved?.archived && organizationId ? (
                                    // Restoring rather than adding: the archived
                                    // profile still owns the name, so a second
                                    // "Use in chats" would only ever 409 on it.
                                    <Button
                                        type="button"
                                        variant="quiet"
                                        size="sm"
                                        className="gap-1.5 px-2"
                                        onClick={() => {
                                            void restore
                                                .mutateAsync({
                                                    organizationId,
                                                    profileId: saved.id,
                                                })
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
                                ) : saved ? (
                                    <span className="flex items-center gap-1.5 text-xs text-[var(--state-success)]">
                                        <Check className="size-3.5" />
                                        Added as {saved.name}
                                    </span>
                                ) : organizationId ? (
                                    <Button
                                        type="button"
                                        variant="quiet"
                                        size="sm"
                                        className="gap-1.5 px-2"
                                        onClick={() => setDialog({ mode: "create", harness })}
                                    >
                                        <Plus className="size-3.5" />
                                        Use in chats
                                    </Button>
                                ) : null}
                            </div>
                        );
                    })}

                    {/*
                     * Three honest states, because the previous version showed
                     * one — "no agents found" — whether it was still connecting,
                     * still scanning, or genuinely finished with nothing.
                     */}
                    {!detected.length ? (
                        <p className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-2.5 text-sm text-[var(--text-tertiary)]">
                            {!status?.available
                                ? "Looking for the Agent Host…"
                                : !status.paired
                                  ? "Connecting this computer…"
                                  : harnesses.isLoading || harnesses.isFetching
                                    ? "Scanning for installed agents…"
                                    : host && isDiscoveringHarnesses(host, detected.length)
                                      ? "Still looking for coding agents on this Mac…"
                                      : "No coding agents found. macOS may ask for file access the first time one is probed — allow it and press Rescan."}
                        </p>
                    ) : null}
                </section>

                <section className="space-y-3">
                    <p className="text-xs font-medium text-[var(--text-tertiary)]">
                        Or connect a model provider
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {PRESETS.map((candidate) => (
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
                    everyone. A coding agent stays on this Mac and uses its own credentials.
                </p>

                <div className="flex flex-wrap items-center gap-3 pt-1">
                    <SetupPrimaryButton
                        type="button"
                        onClick={() => onContinue(configured ? "ready" : "deferred")}
                        className="!mx-0"
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
