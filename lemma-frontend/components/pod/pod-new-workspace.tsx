'use client';

import { useMemo, useState, type ComponentType, type ReactNode } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import {
    ArrowRight,
    BarChart3,
    Bot,
    Table,
    FileSpreadsheet,
    FileText,
    Image as ImageIcon,
    LayoutDashboard,
    MessageCircle,
    NotebookPen,
    PanelsTopLeft,
    Plug,
    Presentation,
    Sparkles,
    Workflow,
} from '@/components/ui/icons';

import { ConnectorIcon } from '@/components/connectors/connector-icon';
import { formatRelativeTime } from '@/components/pod/recent-conversations';
import { THEME_LOGOS } from '@/components/recipes/starter-theme-card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { buildScopedConversationHref } from '@/lib/assistant/conversation-composer-context';
import { requestConversationStageNavigation } from '@/lib/assistant/conversation-presentation';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodStartSignals } from '@/lib/hooks/use-pod-start-signals';
import { buildPodDoActions, buildPodFacts } from '@/lib/pods/pod-start-signals';
import {
    FEATURED_STARTER_THEMES,
    recipesForTheme,
    type RecipeAccent,
    type StarterTheme,
} from '@/lib/recipes/recipes';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';
import { formatAgentName } from '@/lib/utils/agents';
import { Skeleton } from '@/components/shared/loading';

// A launcher, not a landing page.
//
// Opening a new tab is four different intents wearing one coat: make software
// (Build), produce something now (Create), act on what this pod holds (Do), get
// back to a thread (Continue). Each is a tab, so the screen shows one coherent
// set of moves rather than all of them at once, and tabs appear only once the
// pod has something behind them.
//
// Everything inside a tab is the SAME tile — accent-tinted icon, title, hint —
// laid out in the same five-up grid, with the last slot always the way out to
// the full list. Build keeps Home's themed-starter interaction and data, but
// wears the launcher's chrome instead of its own, because four tabs that each
// invent their own card is what made this read as three unrelated screens.

type LauncherTab = 'build' | 'create' | 'do' | 'continue';

type TileIcon = ComponentType<{ className?: string; strokeWidth?: number }>;

interface LauncherTileSpec {
    id: string;
    title: string;
    hint: string;
    accent?: RecipeAccent;
    icon?: TileIcon;
    image?: { src: string; alt: string };
}

interface CreateFormat extends LauncherTileSpec {
    /** A stem the composer focuses at the end of, for the reader to finish. */
    prompt: string;
}

// Each of these lands in the agent's workspace, which ships pandas, openpyxl,
// matplotlib and pillow, a working pip, and a headless Chromium for printing.
const CREATE_FORMATS: CreateFormat[] = [
    { id: 'deck', title: 'Deck', hint: 'Slides', accent: 'delight', icon: Presentation, prompt: 'Create a slide deck about ' },
    { id: 'doc', title: 'Document', hint: 'Write-up', accent: 'info', icon: FileText, prompt: 'Write a document about ' },
    { id: 'sheet', title: 'Spreadsheet', hint: 'Rows and formulas', accent: 'success', icon: FileSpreadsheet, prompt: 'Build a spreadsheet that ' },
    { id: 'chart', title: 'Chart', hint: 'From pod data', accent: 'intelligence', icon: BarChart3, prompt: 'Make a chart showing ' },
    { id: 'report', title: 'PDF report', hint: 'Printable', accent: 'brand', icon: FileText, prompt: 'Produce a PDF report on ' },
    { id: 'widget', title: 'Widget', hint: 'Live, in the chat', accent: 'collaboration', icon: LayoutDashboard, prompt: 'Show me a widget of ' },
    { id: 'image', title: 'Image', hint: 'Rendered', accent: 'delight', icon: ImageIcon, prompt: 'Generate an image of ' },
    { id: 'brief', title: 'Brief', hint: 'The short version', accent: 'info', icon: NotebookPen, prompt: 'Write a short brief on ' },
];

const THEME_ACCENTS: Record<string, RecipeAccent> = {
    dashboards: 'brand',
    whatsapp: 'success',
    telegram: 'info',
    slack: 'delight',
    email: 'collaboration',
};

const BUILD_STEMS: Array<{ id: string; label: string; prompt: string; icon: TileIcon }> = [
    { id: 'app', label: 'Describe an app', prompt: 'Build an app that ', icon: PanelsTopLeft },
    { id: 'workflow', label: 'Describe a workflow', prompt: 'Create a workflow that ', icon: Workflow },
    { id: 'agent', label: 'Describe an agent', prompt: 'Create an agent that ', icon: Bot },
];

// Five to a row, and the last slot is always "there is more of this elsewhere".
// Build fills its second row with the selected theme's prompts; the pod's own
// tabs fill theirs with more of the pod, which is both more useful and what
// keeps the four panels close to a common height.
const TILES_PER_ROW = 5;
const BUILD_THEME_TILES = TILES_PER_ROW - 1;
const POD_TILES = TILES_PER_ROW * 2 - 1;
const MAX_PROMPT_ROWS = 2;

/**
 * Where the launcher sits relative to the composer, which is the only thing
 * that changes between its two homes.
 *
 * `empty-state` — the new-conversation screen, launcher ABOVE the composer.
 * `below-composer` — pod home, where the composer is the hero and this is the
 * tray under it.
 */
export type PodNewWorkspacePlacement = 'empty-state' | 'below-composer';

// A FIXED height, not a floor. Above the composer the two are centred as one
// group, so a panel taller than its neighbour moves the whole block — including
// the pod line and the tabs you are aiming at — every time you switch tabs.
//
// Sized for the worst realistic panel, not the average one: Do, at two rows of
// tiles whose titles have wrapped to the second line line-clamp allows, plus
// the row that unscopes the composer. Measured, not guessed — a tile is 94px
// with a one-line title and 112px with two, so that worst case is 272.
// Undershooting this is what put a scrollbar in the Create panel and clipped
// its caption. `overflow-y-auto` stays as the safety net for anything longer.
const PANEL_HEIGHT = 'h-[17.5rem] overflow-y-auto';

// Below the composer there is nothing above to shove around, so the lock buys
// nothing and costs a block of dead space under every short panel.
const PANEL_HEIGHT_NATURAL = 'min-h-0';

function formatPodName(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return null;
    return cleaned
        .split(' ')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function TileIconBox({ spec, muted }: { spec: LauncherTileSpec; muted?: boolean }) {
    const Icon = spec.icon ?? Sparkles;

    if (muted) {
        return (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-tertiary)]">
                <Icon className="h-3.5 w-3.5" strokeWidth={1.8} />
            </span>
        );
    }

    return (
        <span className="recipe-icon-tile h-7 w-7 shrink-0 rounded-lg" data-accent={spec.accent ?? 'intelligence'}>
            {spec.image ? (
                <Image src={spec.image.src} alt="" aria-hidden width={16} height={16} className="object-contain" />
            ) : (
                <Icon className="h-3.5 w-3.5" strokeWidth={1.8} />
            )}
        </span>
    );
}

/** The only tile on this screen. Every tab fills the same grid with it. */
function LauncherTile({
    spec,
    selected,
    muted,
    disabled,
    href,
    onSelect,
}: {
    spec: LauncherTileSpec;
    selected?: boolean;
    muted?: boolean;
    disabled?: boolean;
    href?: string;
    onSelect?: () => void;
}) {
    const body = (
        <>
            <TileIconBox spec={spec} muted={muted} />
            <span className="flex w-full min-w-0 flex-col items-start gap-0.5">
                <span className="line-clamp-2 w-full text-left text-sm leading-tight text-[var(--text-primary)]">{spec.title}</span>
                <span className="w-full truncate text-left text-xs leading-4 text-[var(--text-tertiary)]">{spec.hint}</span>
            </span>
        </>
    );
    // `whitespace-normal` is load-bearing: Button's base sets `whitespace-nowrap`,
    // which silently clips every title mid-word instead of letting it wrap.
    const className = 'h-auto min-h-20 w-full flex-col items-start justify-start gap-2 whitespace-normal rounded-lg p-2.5 text-left font-normal data-[selected=true]:border-[color:var(--action-primary)]';

    if (href) {
        return (
            <Button asChild variant="secondary" className={className} data-selected={selected ? 'true' : 'false'}>
                <Link
                    href={href}
                    onClick={(event) => {
                        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                        if (requestConversationStageNavigation(href)) event.preventDefault();
                    }}
                >
                    {body}
                </Link>
            </Button>
        );
    }

    return (
        <Button
            type="button"
            variant="secondary"
            onClick={onSelect}
            disabled={disabled}
            data-selected={selected ? 'true' : 'false'}
            className={className}
        >
            {body}
        </Button>
    );
}

function TileGrid({ children }: { children: ReactNode }) {
    return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">{children}</div>;
}

/** Only for full sentences — the themed starter prompts. */
function PromptRow({
    label,
    disabled,
    onSelect,
}: {
    label: string;
    disabled: boolean;
    onSelect: () => void;
}) {
    return (
        <Button
            type="button"
            variant="quiet"
            onClick={onSelect}
            disabled={disabled}
            className="h-auto min-h-9 w-full justify-start gap-2.5 rounded-lg px-2 py-1.5 text-left font-normal"
        >
            <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--intelligence)]" strokeWidth={1.8} />
            <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-secondary)]">{label}</span>
        </Button>
    );
}

function BuildPanel({
    disabled,
    onPreparePrompt,
    onLaunchThemePrompt,
}: {
    disabled: boolean;
    onPreparePrompt: (prompt: string) => void;
    onLaunchThemePrompt: (theme: StarterTheme, recipeId: string, prompt: string) => void;
}) {
    const themes = FEATURED_STARTER_THEMES.slice(0, BUILD_THEME_TILES);
    // Nothing is chosen until someone chooses it. The prompts below still need a
    // theme to come from, so they fall back to the first one — but DRAWING that
    // fallback as selected told everyone opening a new tab that a choice they
    // never made had already been made for them, and the first tile has no
    // claim to being the answer.
    const [chosenThemeId, setChosenThemeId] = useState<string | null>(null);
    const activeTheme = themes.find((theme) => theme.id === chosenThemeId) ?? themes[0];

    if (!activeTheme) return null;

    // A theme may name prompts for recipes it does not actually carry; those
    // would launch into a recipe the panel never offered.
    const themeRecipeIds = new Set(recipesForTheme(activeTheme).map((recipe) => recipe.id));
    const prompts = activeTheme.promptExamples
        .filter((example) => themeRecipeIds.has(example.recipeId))
        .slice(0, MAX_PROMPT_ROWS);

    return (
        <>
            <TileGrid>
                {themes.map((theme) => (
                    <LauncherTile
                        key={theme.id}
                        spec={{
                            id: theme.id,
                            title: theme.name,
                            hint: theme.examples?.[0] ?? 'Starters',
                            accent: THEME_ACCENTS[theme.id] ?? 'intelligence',
                            icon: LayoutDashboard,
                            image: THEME_LOGOS[theme.id],
                        }}
                        selected={theme.id === chosenThemeId}
                        onSelect={() => setChosenThemeId(theme.id)}
                    />
                ))}
            </TileGrid>

            <div className="mt-2 flex flex-col">
                {prompts.map((example, index) => (
                    <PromptRow
                        key={`${example.recipeId}-${index}`}
                        label={example.title}
                        disabled={disabled}
                        onSelect={() => onLaunchThemePrompt(activeTheme, example.recipeId, example.prompt)}
                    />
                ))}
            </div>

            <div className="mt-1 flex flex-wrap items-center gap-1">
                {BUILD_STEMS.map((stem) => (
                    <Button
                        key={stem.id}
                        type="button"
                        variant="quiet"
                        size="sm"
                        onClick={() => onPreparePrompt(stem.prompt)}
                        disabled={disabled}
                        className="h-8 min-h-8 w-auto justify-start gap-2 rounded-md px-2 font-normal"
                    >
                        <stem.icon className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" strokeWidth={1.8} />
                        <span className="truncate text-sm text-[var(--text-secondary)]">{stem.label}</span>
                    </Button>
                ))}
            </div>
        </>
    );
}

export function PodNewWorkspace({
    podId,
    selectedAgentName,
    onPreparePrompt,
    onSelectAgent,
    placement = 'empty-state',
}: {
    podId: string;
    /** Agent the composer is currently scoped to, or `null` for the pod default. */
    selectedAgentName: string | null;
    onPreparePrompt: (prompt: string) => void;
    onSelectAgent: (agentName: string | null) => void;
    placement?: PodNewWorkspacePlacement;
}) {
    // Below the composer the pod is already named by the heading above it, and
    // nothing sits above the panel for a height change to disturb.
    const isBelowComposer = placement === 'below-composer';
    const panelHeight = isBelowComposer ? PANEL_HEIGHT_NATURAL : PANEL_HEIGHT;
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const podName = formatPodName(pod?.name);
    const { launchRecipe } = useLaunchRecipe(podId, { podName });
    const { signals, recentConversations, isLoading } = usePodStartSignals(podId);
    const canWriteConversations = podAccess.can('conversation.write');
    const disabled = !canWriteConversations;

    const facts = useMemo(() => buildPodFacts(signals), [signals]);
    const doActions = useMemo(() => buildPodDoActions(signals), [signals]);
    const agents = signals.agents;
    const connectors = signals.connectors;
    const continueRows = recentConversations.slice(0, POD_TILES);

    const doTiles = useMemo<LauncherTileSpec[]>(() => {
        const resourceTiles = doActions.map((action) => ({
            id: action.id,
            title: action.label,
            hint: action.id.startsWith('workflow:') ? 'Workflow' : 'Table',
            accent: (action.id.startsWith('workflow:') ? 'success' : 'info') as RecipeAccent,
            icon: action.id.startsWith('workflow:') ? Workflow : Table,
        }));
        const agentTiles = agents.map((agent) => ({
            id: `agent:${agent.name}`,
            title: formatAgentName(agent.name),
            hint: selectedAgentName === agent.name ? 'Selected' : 'Hand this over',
            accent: 'intelligence' as RecipeAccent,
            icon: Bot,
        }));
        return [...resourceTiles, ...agentTiles].slice(0, POD_TILES);
    }, [agents, doActions, selectedAgentName]);

    const promptByActionId = useMemo(
        () => new Map(doActions.map((action) => [action.id, action.prompt])),
        [doActions],
    );

    const hasDo = doTiles.length > 0;
    const hasContinue = continueRows.length > 0;
    const [tab, setTab] = useState<LauncherTab>('build');
    const activeTab: LauncherTab =
        (tab === 'do' && !hasDo) || (tab === 'continue' && !hasContinue) ? 'build' : tab;

    return (
        <div className="flex w-full flex-col gap-3 text-[var(--text-primary)]">
            {isBelowComposer ? null : (
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5">
                {podName ? (
                    <span className="text-sm font-medium text-[var(--text-primary)]">{podName}</span>
                ) : null}
                {isLoading ? (
                    <Skeleton shape="block" className="h-3 w-56" />
                ) : (
                    <>
                        {facts.length > 0 ? (
                            <span className="min-w-0 text-sm leading-5 text-[var(--text-tertiary)]">
                                {facts.join(' · ')}
                            </span>
                        ) : null}
                        {connectors.length > 0 ? (
                            <span className="flex min-w-0 flex-wrap items-center gap-1">
                                {connectors.map((connector) => (
                                    <ConnectorIcon
                                        key={connector.connectorId}
                                        connectorId={connector.connectorId}
                                        icon={connector.icon}
                                        label={connector.label}
                                        size="sm"
                                        className="h-6 w-6 rounded-md p-1"
                                    />
                                ))}
                            </span>
                        ) : null}
                    </>
                )}
            </div>
            )}

            <Tabs value={activeTab} onValueChange={(value) => setTab(value as LauncherTab)} className="min-w-0">
                <TabsList>
                    <TabsTrigger value="build">Build</TabsTrigger>
                    <TabsTrigger value="create">Create</TabsTrigger>
                    {hasDo ? <TabsTrigger value="do">Do</TabsTrigger> : null}
                    {hasContinue ? <TabsTrigger value="continue">Continue</TabsTrigger> : null}
                </TabsList>

                {/* A floor under every panel keeps the composer still between tabs. */}
                <TabsContent value="build" className={panelHeight}>
                    <BuildPanel
                        disabled={disabled}
                        onPreparePrompt={onPreparePrompt}
                        onLaunchThemePrompt={(theme, recipeId, prompt) => {
                            const recipe = recipesForTheme(theme).find((entry) => entry.id === recipeId);
                            if (recipe) launchRecipe(recipe, { message: prompt });
                        }}
                    />
                </TabsContent>

                <TabsContent value="create" className={panelHeight}>
                    <TileGrid>
                        {CREATE_FORMATS.map((format) => (
                            <LauncherTile
                                key={format.id}
                                spec={format}
                                disabled={disabled}
                                onSelect={() => onPreparePrompt(format.prompt)}
                            />
                        ))}
                    </TileGrid>
                    <p className="mt-2.5 text-xs text-[var(--text-tertiary)]">
                        Made in this pod&rsquo;s files, from this pod&rsquo;s data.
                    </p>
                </TabsContent>

                {hasDo ? (
                    <TabsContent value="do" className={panelHeight}>
                        <TileGrid>
                            {doTiles.map((tile) => (
                                <LauncherTile
                                    key={tile.id}
                                    spec={tile}
                                    selected={tile.id === `agent:${selectedAgentName}`}
                                    disabled={disabled}
                                    onSelect={() => {
                                        const prompt = promptByActionId.get(tile.id);
                                        if (prompt) {
                                            onPreparePrompt(prompt);
                                            return;
                                        }
                                        onSelectAgent(tile.id.replace(/^agent:/, ''));
                                    }}
                                />
                            ))}
                            <LauncherTile
                                spec={{ id: 'connect', title: 'Connect a tool', hint: 'More to work with', icon: Plug }}
                                muted
                                href={`/pod/${podId}/connectors`}
                            />
                        </TileGrid>
                        {selectedAgentName ? (
                            <div className="mt-2">
                                <Button
                                    type="button"
                                    variant="quiet"
                                    size="sm"
                                    onClick={() => onSelectAgent(null)}
                                    disabled={disabled}
                                    className="h-8 min-h-8 w-auto justify-start gap-2 rounded-md px-2 font-normal"
                                >
                                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" strokeWidth={1.8} />
                                    <span className="text-sm text-[var(--text-secondary)]">Back to Lemma Assist</span>
                                </Button>
                            </div>
                        ) : null}
                    </TabsContent>
                ) : null}

                {hasContinue ? (
                    <TabsContent value="continue" className={panelHeight}>
                        <TileGrid>
                            {continueRows.map((conversation) => (
                                <LauncherTile
                                    key={conversation.id}
                                    spec={{
                                        id: conversation.id,
                                        title: conversation.title?.trim() || 'Untitled conversation',
                                        hint: formatRelativeTime(conversation.updated_at ?? conversation.created_at) ?? 'Earlier',
                                        accent: 'collaboration',
                                        icon: MessageCircle,
                                    }}
                                    href={buildScopedConversationHref({
                                        podId,
                                        conversationId: conversation.id,
                                        agentName: selectedAgentName,
                                    })}
                                />
                            ))}
                            <LauncherTile
                                spec={{ id: 'all-conversations', title: 'All conversations', hint: 'Full history', icon: ArrowRight }}
                                muted
                                href={`/pod/${podId}/conversations`}
                            />
                        </TileGrid>
                    </TabsContent>
                ) : null}
            </Tabs>
        </div>
    );
}
