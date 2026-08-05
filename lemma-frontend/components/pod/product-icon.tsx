'use client';

import {
    AppWindow,
    ChatTeardrop,
    Clock,
    Code,
    Cube,
    File,
    Files,
    FlowArrow,
    FolderOpen,
    FolderSimple,
    MagicWand,
    Plug,
    Shield,
    SlidersHorizontal,
    Table,
    Tray,
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
    | 'settings'
    | 'auth-rbac'
    | 'conversation';

const iconByKind: Record<ProductIconKind, typeof FolderOpen> = {
    // A pod is a self-contained unit of data, agents and apps, not a directory —
    // and it cannot share `folders`' glyph, which is what it did before.
    pods: Cube,
    connectors: Plug,
    apps: AppWindow,
    agents: MagicWand,
    workflows: FlowArrow,
    schedules: Clock,
    // `data` is the section and `tables` the resource inside it, so they share a
    // glyph on purpose — the Data page is tables. `docs` and `files` stay apart:
    // a doc is a stack you browse, a file is the single thing you opened.
    data: Table,
    tables: Table,
    docs: Files,
    files: File,
    folders: FolderSimple,
    functions: Code,
    // Surfaces are where work arrives — Slack, Gmail, WhatsApp — so the glyph is
    // an inbox, not a speech bubble. Two bubbles for `surfaces` and
    // `conversation` were indistinguishable at 14px, and they are different
    // things: one is the pipe, the other is the thread that came down it.
    surfaces: Tray,
    settings: SlidersHorizontal,
    'auth-rbac': Shield,
    conversation: ChatTeardrop,
};

/**
 * Every product glyph is a `regular` outline in every state — at rest, under
 * the pointer, and while selected. Weight and fill are deliberately not
 * expressive axes here: a column of these icons is read by silhouette, and a
 * glyph that thickens or fills on interaction changes how much ink one row
 * carries relative to its neighbours, which is exactly what makes a nav look
 * unsettled. Selection is carried by colour and the row's accent bar; the
 * pointer is answered by a small scale in CSS.
 */
export function ProductIcon({
    kind,
    size = 'md',
    state = 'default',
}: {
    kind: ProductIconKind;
    size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
    state?: 'default' | 'selected';
}) {
    const Icon = iconByKind[kind] || FolderOpen;

    return (
        <span
            className="lemma-product-icon"
            data-size={size}
            data-kind={kind}
            data-state={state}
        >
            <Icon weight="regular" className="lemma-product-icon-glyph" />
        </span>
    );
}
