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
    logoSrc?: string;
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
    accountLabel: string;
    capabilities: {
        /** Slack/Teams: per-channel routes, configurable only once the surface
         * exists (channel enumeration needs the connected account). */
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
        logoSrc: '/surfaces/telegram.png',
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
        logoSrc: '/surfaces/whatsapp.png',
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
        logoSrc: '/surfaces/slack.png',
        promise: 'Let people reach {agent} in Slack',
        connectHint: 'Answers DMs, plus any Slack channel you route here.',
        identityOptions: [
            {
                mode: 'SYSTEM',
                title: 'Lemma’s Slack app',
                detail: 'Install Lemma into your workspace. Nothing to register yourself.',
                hint: 'Fastest',
            },
            {
                mode: 'CUSTOM',
                title: 'Your workspace’s own app',
                detail: 'Your Slack app and branding. You’ll point its events at Lemma.',
                hint: '~10 min',
            },
        ],
        accountLabel: 'Slack workspace',
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
        logoSrc: '/surfaces/teams.png',
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
        logoSrc: '/surfaces/gmail.png',
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
        logoSrc: '/surfaces/outlook.png',
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
        promise: 'Give {agent} an email address of its own',
        connectHint: 'An address Lemma runs for you. No mailbox to connect.',
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
