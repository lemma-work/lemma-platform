import { createElement, type ReactElement } from 'react';
import {
    BookOpen,
    Briefcase,
    CalendarDays,
    Contact2,
    Eye,
    Flame,
    Inbox,
    Lightbulb,
    ListChecks,
    type LemmaIcon,
    MessageSquare,
    MessagesSquare,
    Newspaper,
    NotebookPen,
    PackageOpen,
    PanelsTopLeft,
    Receipt,
    RefreshCw,
    Send,
    Sparkles,
    Workflow,
} from '@/components/ui/icons';

import type { Recipe, RecipeBuilds } from '@/lib/recipes/recipes';

const BY_ID: Record<string, LemmaIcon> = {
    // quick wins
    'personal-crm': Contact2,
    'reading-digest': BookOpen,
    'meeting-notes': ListChecks,
    'job-tracker': Briefcase,
    'habit-tracker': Flame,
    'daily-log': NotebookPen,
    // bots & surfaces
    'slack-standup-bot': MessageSquare,
    'expense-logger-bot': Receipt,
    'ask-my-data-bot': MessagesSquare,
    'email-intake-bot': Inbox,
    'site-support-bot': MessagesSquare,
    // creators & indies
    'content-idea-engine': Lightbulb,
    'newsletter-curator': Newspaper,
    'competitor-watch': Eye,
    'lead-finder': Send,
    // consultants
    'client-invoice-tracker': Receipt,
    'proposal-drafter': NotebookPen,
    // team ops
    'renewal-review': RefreshCw,
    'support-triage': MessagesSquare,
    'approvals-queue': ListChecks,
    'standup-digest': CalendarDays,
    'content-pipeline': NotebookPen,
    'lightweight-crm': Contact2,
};

const BY_BUILDS: Record<RecipeBuilds, LemmaIcon> = {
    app: PanelsTopLeft,
    agent: Sparkles,
    workflow: Workflow,
    surface: MessageSquare,
    pod: PackageOpen,
};

function pickRecipeIcon(recipe: Recipe): LemmaIcon {
    if (recipe.source.kind === 'repo') return PackageOpen;
    return BY_ID[recipe.id] ?? BY_BUILDS[recipe.builds] ?? PanelsTopLeft;
}

// Calm monochrome line icons — no emoji. Returns an element (via createElement)
// so callers don't render a component selected during render.
export function renderRecipeIcon(recipe: Recipe, props?: { className?: string; strokeWidth?: number }): ReactElement {
    return createElement(pickRecipeIcon(recipe), props);
}
