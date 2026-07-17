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
        <span className="lemma-product-icon" data-size={size} data-kind={kind} data-state={state}>
            <Icon weight={state === 'selected' ? 'fill' : 'regular'} className="h-full w-full" />
        </span>
    );
}
