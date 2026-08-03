'use client';

import {
    Chat,
    ChatCircle,
    Clock,
    Code,
    Database,
    File,
    FileText,
    FolderOpen,
    Gear,
    GitMerge,
    Plugs,
    Rss,
    ShieldCheck,
    Sparkle,
    SquaresFour,
    Table,
} from '@/components/ui/icons';

export type ProductIconKind =
    | 'pods'
    | 'connectors'
    | 'apps'
    | 'agents'
    | 'workflows'
    | 'schedules'
    | 'data'
    | 'tables'
    | 'docs'
    | 'files'
    | 'folders'
    | 'functions'
    | 'surfaces'
    | 'channels'
    | 'settings'
    | 'auth-rbac'
    | 'conversation';

const iconByKind: Record<ProductIconKind, typeof FolderOpen> = {
    pods: FolderOpen,
    connectors: Plugs,
    apps: SquaresFour,
    agents: Sparkle,
    workflows: GitMerge,
    schedules: Clock,
    data: Database,
    tables: Table,
    docs: FileText,
    files: File,
    folders: FolderOpen,
    functions: Code,
    surfaces: ChatCircle,
    channels: Rss,
    settings: Gear,
    'auth-rbac': ShieldCheck,
    conversation: Chat,
};

/**
 * Weight is the expressive axis: `regular` at rest, `bold` under the pointer,
 * `fill` once selected. Phosphor swaps path data per weight rather than
 * scaling a stroke, so the pointer step cannot be a CSS property — it is a
 * second glyph stacked on the first and crossfaded by the row that owns the
 * hover. A selected icon is already the loudest thing in its row, so it opts
 * out of the pointer layer and stays a single glyph.
 *
 * `interactive` is opt-in: identity icons that merely label a page header stay
 * inert and render one glyph, the same as before.
 */
export function ProductIcon({
    kind,
    size = 'md',
    state = 'default',
    interactive = false,
}: {
    kind: ProductIconKind;
    size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
    state?: 'default' | 'selected';
    interactive?: boolean;
}) {
    const Icon = iconByKind[kind] || FolderOpen;
    const selected = state === 'selected';
    const respondsToPointer = interactive && !selected;

    return (
        <span
            className="lemma-product-icon"
            data-size={size}
            data-kind={kind}
            data-state={state}
            data-interactive={respondsToPointer ? 'true' : undefined}
        >
            <Icon
                weight={selected ? 'fill' : 'regular'}
                className="lemma-product-icon-glyph"
                data-layer="rest"
            />
            {respondsToPointer ? (
                <Icon weight="bold" className="lemma-product-icon-glyph" data-layer="pointer" />
            ) : null}
        </span>
    );
}
