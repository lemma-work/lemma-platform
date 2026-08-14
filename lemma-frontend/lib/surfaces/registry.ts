import { Mail, type LemmaIcon } from '@/components/ui/icons';

import type { SurfacePlatformValue } from '@/lib/hooks/use-pod-surfaces';

/**
 * What each surface platform needs from the setup UI, in one place.
 *
 * The catalog endpoint (`agent.surface.available`) is authoritative for anything
 * that varies by *deployment* — whether a Lemma-managed bot exists here, the
 * connector's credential schema, whether the org already claimed the shared
 * identity. This registry holds what varies by *platform* and never by
 * deployment: the words, the journey, and which capabilities the shape of the
 * modal turns on. Adding a platform means adding one entry here plus, if it has
 * a bring-your-own journey, its steps.
 */

/**
 * Whose bot/number/workspace a surface runs on.
 *
 * `MANAGED` is distinct from `CUSTOM`: Lemma provisions a dedicated bot for the
 * user through a manager bot, so they end up owning it without ever handling a
 * token. `CUSTOM` is the fallback for an account they connected themselves.
 */
export type SurfaceIdentityMode = 'SYSTEM' | 'MANAGED' | 'CUSTOM';

/** One instruction in a bring-your-own journey. `field` marks the step that owns
 * the credential input, so the input renders inside the step rather than under
 * a wall of prose. */
export interface SurfaceJourneyStep {
    text: string;
    link?: string;
    linkLabel?: string;
    field?: string;
    optional?: boolean;
}

export interface SurfaceIdentityOption {
    mode: SurfaceIdentityMode;
    title: string;
    /** The consequence of choosing this, in one line. */
    detail: string;
    /** Right-aligned hint: effort, or why you'd pick it. */
    hint?: string;
}

export interface SurfacePlatformDefinition {
    platform: SurfacePlatformValue;
    label: string;
    /**
     * The platform's own brand mark. SVG on purpose: these render at 14–20px in
     * chips, and the raster set they replaced included a Gmail file with no
     * alpha channel — a white rectangle at any size, and a visible block in dark
     * mode. Shared with the connector catalog, which draws the same six brands.
     */
    logoSrc?: string;
    /**
     * Drawn instead of a logo, for the platform that is not a brand.
     *
     * Email-of-its-own is Lemma's, not a vendor's — there is no mark to borrow,
     * and borrowing Gmail's (which the recipe previews did) says the address
     * lives in a Google mailbox, which is the one thing it doesn't. A glyph also
     * takes `currentColor`, so it is the only mark here that themes.
     */
    glyph?: LemmaIcon;
    /** Second-person promise for the modal header. `{agent}` is substituted. */
    promise: string;
    /** Shown on the agent page when this platform isn't connected yet. */
    connectHint: string;
    /** Null when the platform has no identity fork (one way in, no question). */
    identityOptions: SurfaceIdentityOption[] | null;
    /** The bring-your-own walkthrough, if it has one. */
    journey?: {
        title: string;
        steps: SurfaceJourneyStep[];
    };
    /**
     * What a person does next, once the surface is live — shown in the proof
     * state. Only for platforms where the remaining setup happens *there* and
     * not here, which is why it reads as orientation rather than a checklist.
     */
    afterConnect?: {
        title: string;
        lines: string[];
    };
    accountLabel: string;
    capabilities: {
        /**
         * Slack/Teams: reach is a *channel*, not the bot.
         *
         * One workspace install carries many channels, each routable to its own
         * agent, so the agent page shows a chip per channel and keeps offering
         * "add another" — where an identity platform shows one chip and stops.
         * Routes are configurable only once the surface exists, because
         * enumerating channels needs the connected account.
         */
        channelRoutes: boolean;
        /** Gmail/Outlook/Resend: sender filters decide what becomes pod work. */
        senderFilters: boolean;
        /** Teams: a tenant admin approves the app once. */
        adminConsent: boolean;
        /** Lemma registers the webhook itself — say so instead of showing steps
         * the user doesn't have to do. */
        autoWebhook: boolean;
    };
}

const DEFINITIONS: SurfacePlatformDefinition[] = [
    {
        platform: 'TELEGRAM',
        label: 'Telegram',
        logoSrc: '/connector-logos/telegram.svg',
        promise: 'Let people reach {agent} on Telegram',
        connectHint: 'A Telegram bot of your own. People message it directly.',
        // Every Telegram bot is the user's own now, so the question isn't whose
        // bot it is — it's whether they need a new one. Making a new one is the
        // path almost everyone takes, so it leads.
        identityOptions: [
            {
                mode: 'MANAGED',
                title: 'Create a bot',
                detail: 'You name it in Telegram and it’s yours. Nothing to copy back here.',
                hint: '~1 min',
            },
            {
                mode: 'CUSTOM',
                title: 'Use a bot you’ve connected',
                detail: 'Point an existing Telegram bot at {agent} instead.',
            },
        ],
        accountLabel: 'Telegram bot',
        capabilities: {
            channelRoutes: false,
            senderFilters: false,
            adminConsent: false,
            autoWebhook: true,
        },
    },
    {
        platform: 'WHATSAPP',
        label: 'WhatsApp',
        logoSrc: '/connector-logos/whatsapp.svg',
        promise: 'Let people reach {agent} on WhatsApp',
        connectHint: 'A WhatsApp number people message like any other contact.',
        identityOptions: [
            {
                mode: 'SYSTEM',
                title: 'Lemma number',
                detail: 'Lemma’s shared number answers as {agent}. One pod per org can use it.',
                hint: 'Fastest',
            },
            {
                mode: 'CUSTOM',
                title: 'Your own number',
                detail: 'Your Meta Business number. You’ll finish the webhook in Meta.',
                hint: '~15 min',
            },
        ],
        // Only what Meta can give you *before* the surface exists. The webhook
        // half of the setup needs a callback URL and verify token that Lemma
        // only mints once the surface is created, so it arrives as setup actions
        // from the backend rather than living here.
        journey: {
            title: 'Copy these from your Meta app',
            steps: [
                {
                    text: 'Open your app on Meta for Developers, then WhatsApp → API Setup',
                    link: 'https://developers.facebook.com/apps',
                    linkLabel: 'Open Meta',
                },
                { text: 'Generate a permanent access token for the number' },
                { text: 'Paste it here with the number and business account IDs', field: 'access_token' },
                {
                    text: 'Add the app secret too, so Lemma can verify Meta’s signatures',
                    optional: true,
                },
            ],
        },
        accountLabel: 'WhatsApp account',
        capabilities: {
            channelRoutes: false,
            senderFilters: false,
            adminConsent: false,
            autoWebhook: false,
        },
    },
    {
        platform: 'SLACK',
        label: 'Slack',
        logoSrc: '/connector-logos/slack.svg',
        promise: 'Let people reach {agent} in Slack',
        connectHint: 'Add Lemma to your workspace, then invite it to a channel.',
        // No identity fork *at connect time*. Connecting installs a Slack app
        // into a workspace — asked once per workspace, so there is nothing to
        // fork on. An org that would rather run its own Slack app sets that up
        // in Connectors, where the app's credentials live: it is one app for the
        // org, not a choice each surface makes.
        //
        // Reach is per *channel*, not per bot (`capabilities.channelRoutes`),
        // and DMs are per *person*: everyone picks the agent that answers them
        // from the App Home, so no one agent holds a workspace's DMs.
        identityOptions: null,
        accountLabel: 'Slack workspace',
        // The rest of setup happens in Slack, so this is where someone finds
        // out that it does — the alternative is a settings page that looks like
        // the only way in, which is what this screen used to imply.
        afterConnect: {
            title: 'The rest happens in Slack',
            lines: [
                'Invite Lemma to a channel and it’ll ask who should answer there.',
                'Everyone picks who answers their own messages, from Lemma’s home tab in Slack.',
                'Answers appear as they’re written, with tables, headings and code.',
            ],
        },
        capabilities: {
            channelRoutes: true,
            senderFilters: false,
            adminConsent: false,
            autoWebhook: false,
        },
    },
    {
        platform: 'TEAMS',
        label: 'Teams',
        logoSrc: '/connector-logos/teams.svg',
        promise: 'Let people reach {agent} in Teams',
        connectHint: 'Answers chats, plus any Teams channel you route here.',
        identityOptions: null,
        accountLabel: 'Microsoft tenant',
        capabilities: {
            channelRoutes: true,
            senderFilters: false,
            adminConsent: true,
            autoWebhook: false,
        },
    },
    {
        platform: 'GMAIL',
        label: 'Gmail',
        logoSrc: '/connector-logos/gmail.svg',
        promise: 'Turn mail in a Gmail mailbox into work for {agent}',
        connectHint: 'Mail from a mailbox you connect becomes work here.',
        identityOptions: null,
        accountLabel: 'Gmail mailbox',
        capabilities: {
            channelRoutes: false,
            senderFilters: true,
            adminConsent: false,
            autoWebhook: true,
        },
    },
    {
        platform: 'OUTLOOK',
        label: 'Outlook',
        logoSrc: '/connector-logos/outlook.svg',
        promise: 'Turn mail in an Outlook mailbox into work for {agent}',
        connectHint: 'Mail from a mailbox you connect becomes work here.',
        identityOptions: null,
        accountLabel: 'Outlook mailbox',
        capabilities: {
            channelRoutes: false,
            senderFilters: true,
            adminConsent: false,
            autoWebhook: true,
        },
    },
    {
        platform: 'RESEND',
        label: 'Email',
        // No logo, by design — see `glyph`. Lemma runs this mailbox; there is no
        // vendor whose mark belongs on it.
        glyph: Mail,
        promise: 'Give {agent} an email address of its own',
        // Every agent already has one. This chip exists for the pod that turned
        // its address off, or a deployment that had no mail domain when the
        // agent was made — so it reads as "back on", not "new".
        connectHint: 'The address Lemma already runs for this agent.',
        identityOptions: null,
        accountLabel: 'Managed address',
        capabilities: {
            channelRoutes: false,
            senderFilters: true,
            adminConsent: false,
            autoWebhook: true,
        },
    },
];

const BY_PLATFORM = new Map<string, SurfacePlatformDefinition>(
    DEFINITIONS.map((definition) => [definition.platform, definition]),
);

/** Display order — the two one-tap platforms first, then workspaces, then email. */
export const SURFACE_PLATFORM_ORDER: SurfacePlatformValue[] = DEFINITIONS.map(
    (definition) => definition.platform,
);

export function getSurfaceDefinition(
    platform: string | null | undefined,
): SurfacePlatformDefinition | null {
    if (!platform) return null;
    return BY_PLATFORM.get(platform.toUpperCase()) ?? null;
}

/** Substitutes the agent's name into registry copy. `null` = the pod default. */
export function forAgent(copy: string, agentName: string | null): string {
    return copy.replaceAll('{agent}', agentName || 'the pod assistant');
}
