import Image from 'next/image';

import type { Recipe, RecipePreviewKind } from '@/lib/recipes/recipes';
import { cn } from '@/lib/utils';

const PLATFORM_PREVIEWS = new Set<RecipePreviewKind>(['whatsapp', 'telegram', 'slack', 'email', 'teams']);

const PLATFORM_META: Partial<Record<RecipePreviewKind, { label: string; logo: string }>> = {
    whatsapp: { label: 'WhatsApp', logo: '/surfaces/whatsapp.png' },
    telegram: { label: 'Telegram', logo: '/surfaces/telegram.png' },
    slack: { label: 'Slack', logo: '/surfaces/slack.png' },
    email: { label: 'Gmail + Outlook', logo: '/surfaces/gmail.png' },
    teams: { label: 'Microsoft Teams', logo: '/surfaces/teams.png' },
};

export function StarterPreview({ recipe, compact = false }: { recipe: Recipe; compact?: boolean }) {
    if (PLATFORM_PREVIEWS.has(recipe.preview)) {
        return <PlatformPreview preview={recipe.preview} compact={compact} />;
    }

    return (
        <div
            aria-hidden="true"
            className={cn('starter-preview', compact && 'starter-preview-compact')}
            data-preview={recipe.preview}
        >
            <div className="starter-preview-window-bar">
                <span />
                <span />
                <span />
                <span className="starter-preview-window-title">{recipe.kicker}</span>
            </div>
            <div className="starter-preview-canvas">
                <ShapePreview preview={recipe.preview} />
            </div>
        </div>
    );
}

function PlatformPreview({ preview, compact }: { preview: RecipePreviewKind; compact: boolean }) {
    const meta = PLATFORM_META[preview];

    return (
        <div
            aria-hidden="true"
            className={cn('starter-preview starter-platform-preview', compact && 'starter-preview-compact')}
            data-preview={preview}
        >
            <div className="starter-platform-header">
                <span className="starter-platform-logo">
                    {meta ? <Image src={meta.logo} alt="" width={18} height={18} /> : null}
                </span>
                <span className="starter-platform-label">{meta?.label}</span>
                <span className="starter-platform-live"><i /> Live</span>
            </div>
            <div className="starter-platform-body">
                <div className="starter-platform-thread">
                    <span className="starter-chat-bubble starter-chat-bubble-user" />
                    <span className="starter-chat-bubble starter-chat-bubble-agent" />
                    <span className="starter-chat-bubble starter-chat-bubble-agent starter-chat-bubble-short" />
                </div>
                <div className="starter-platform-companion">
                    <span className="starter-companion-kicker">Needs you</span>
                    <span className="starter-companion-title" />
                    <span className="starter-companion-line" />
                    <span className="starter-companion-actions">
                        <i />
                        <i />
                    </span>
                </div>
            </div>
        </div>
    );
}

function ShapePreview({ preview }: { preview: RecipePreviewKind }) {
    if (preview === 'dashboard') {
        return (
            <div className="starter-dashboard-preview">
                <div className="starter-mini-sidebar"><i /><i /><i /><i /></div>
                <div className="starter-dashboard-main">
                    <div className="starter-dashboard-heading"><span /><i /></div>
                    <div className="starter-dashboard-metrics"><span /><span /><span /></div>
                    <div className="starter-dashboard-table"><i /><i /><i /></div>
                </div>
            </div>
        );
    }

    if (preview === 'inbox' || preview === 'triage') {
        return (
            <div className="starter-inbox-preview">
                <div className="starter-inbox-list"><i /><i /><i /></div>
                <div className="starter-inbox-detail">
                    <span className="starter-inbox-status">Prepared by agent</span>
                    <strong />
                    <i /><i />
                    <div><b /><b /></div>
                </div>
            </div>
        );
    }

    if (preview === 'knowledge') {
        return (
            <div className="starter-knowledge-preview">
                <div className="starter-knowledge-tree"><i /><i /><i /><i /></div>
                <div className="starter-knowledge-doc"><strong /><span /><span /><span /><div /></div>
            </div>
        );
    }

    if (preview === 'portal') {
        return (
            <div className="starter-portal-preview">
                <strong>Tell us what you need</strong>
                <span />
                <span />
                <span className="starter-portal-field-wide" />
                <i>Send request</i>
            </div>
        );
    }

    if (preview === 'monitor') {
        return (
            <div className="starter-monitor-preview">
                <div className="starter-monitor-heading"><strong>Watching 12 signals</strong><i /></div>
                <div className="starter-monitor-chart"><i /><i /><i /><i /><i /><i /></div>
                <div className="starter-monitor-alert"><b /><span><strong>Meaningful change</strong><i /></span></div>
            </div>
        );
    }

    if (preview === 'approval') {
        return (
            <div className="starter-approval-preview">
                <span className="starter-approval-kicker">Decision waiting</span>
                <strong />
                <i /><i />
                <div><b /><b /></div>
            </div>
        );
    }

    if (preview === 'briefing') {
        return (
            <div className="starter-briefing-preview">
                <div><span>MON</span><strong>9:00</strong></div>
                <section><b /><i /><i /><i /><i /></section>
            </div>
        );
    }

    if (preview === 'follow-up') {
        return (
            <div className="starter-follow-up-preview">
                <div><b /><span><strong /><i /></span><em>Today</em></div>
                <div><b /><span><strong /><i /></span><em>2d</em></div>
                <div><b /><span><strong /><i /></span><em>5d</em></div>
            </div>
        );
    }

    return (
        <div className="starter-kit-preview">
            <span /><span /><span /><span />
            <i /><i /><i />
        </div>
    );
}
