import type { Connector } from '@/lib/types';

/**
 * Curated grouping for the connector catalog.
 *
 * The catalog itself carries no category field, so this map is the product's
 * editorial layer over it. Anything unmapped falls through to "More apps"
 * rather than disappearing, so a catalog import that adds new toolkits
 * degrades to an ungrouped tail instead of breaking the page.
 */
export const FEATURED_CONNECTOR_IDS = [
    'slack',
    'gmail',
    'google_calendar',
    'notion',
    'linear',
    'github',
    'hubspot',
    'google_sheets',
] as const;

const CATEGORY_MEMBERS: Array<{ title: string; blurb: string; ids: string[] }> = [
    {
        title: 'Messaging',
        blurb: 'Where your team already talks — put agents in the same room.',
        ids: [
            'slack',
            'microsoft_teams',
            'telegram',
            'whatsapp',
            'discord',
            'google_chat',
            'zoom',
            'googlemeet',
        ],
    },
    {
        title: 'Email & calendar',
        blurb: 'Read, draft, schedule, and follow up.',
        ids: [
            'gmail',
            'outlook',
            'google_calendar',
            'googlecontacts',
            'zoho_mail',
            'resend',
            'cal',
            'calendly',
            'googletasks',
        ],
    },
    {
        title: 'Projects & issues',
        blurb: 'Plan, track, and close the loop on work.',
        ids: [
            'jira',
            'confluence',
            'linear',
            'asana',
            'trello',
            'clickup',
            'monday',
            'todoist',
            'notion',
            'miro',
            'figma',
            'canva',
        ],
    },
    {
        title: 'CRM & support',
        blurb: 'Pipeline, customers, and the tickets they open.',
        ids: [
            'hubspot',
            'salesforce',
            'apollo',
            'intercom',
            'zendesk',
            'freshdesk',
            'servicenow',
        ],
    },
    {
        title: 'Files & documents',
        blurb: 'The drives and docs your pods read from.',
        ids: [
            'google_drive',
            'google_docs',
            'google_sheets',
            'googleslides',
            'googleforms',
            'dropbox',
            'box',
            'one_drive',
            'excel',
        ],
    },
    {
        title: 'Analytics',
        blurb: 'Product and traffic data agents can reason over.',
        ids: [
            'posthog',
            'mixpanel',
            'metabase',
            'google_analytics',
            'segment',
            'semrush',
            'sentry',
        ],
    },
    {
        title: 'Finance',
        blurb: 'Payments, invoices, and books.',
        ids: [
            'stripe',
            'paypal',
            'square',
            'quickbooks',
            'razorpay',
            'zoho_books',
            'zoho_invoice',
            'zoho_inventory',
        ],
    },
    {
        title: 'Developer',
        blurb: 'Code, releases, and incidents.',
        ids: ['github'],
    },
    {
        title: 'Marketing & social',
        blurb: 'Campaigns, audiences, and everywhere you publish.',
        ids: [
            'twitter',
            'linkedin',
            'linkedin_ads',
            'instagram',
            'facebook',
            'metaads',
            'googleads',
            'mailchimp',
            'reddit',
            'reddit_ads',
            'tiktok',
            'youtube',
            'spotify',
        ],
    },
];

const CATEGORY_BY_ID = new Map<string, string>();
for (const category of CATEGORY_MEMBERS) {
    for (const id of category.ids) {
        CATEGORY_BY_ID.set(id, category.title);
    }
}

const FALLBACK_CATEGORY = 'More apps';

export interface ConnectorSection {
    title: string;
    blurb?: string;
    connectors: Connector[];
}

/**
 * Splits the catalog into display sections, preserving the caller's ordering
 * within each one. Empty sections are dropped so a filtered search collapses to
 * only the groups that matched.
 */
export const groupConnectors = (connectors: Connector[]): ConnectorSection[] => {
    const buckets = new Map<string, Connector[]>();

    for (const connector of connectors) {
        const title = CATEGORY_BY_ID.get(connector.id) ?? FALLBACK_CATEGORY;
        const bucket = buckets.get(title);
        if (bucket) bucket.push(connector);
        else buckets.set(title, [connector]);
    }

    const sections: ConnectorSection[] = [];
    for (const category of CATEGORY_MEMBERS) {
        const bucket = buckets.get(category.title);
        if (bucket?.length) {
            sections.push({ title: category.title, blurb: category.blurb, connectors: bucket });
        }
    }

    const remainder = buckets.get(FALLBACK_CATEGORY);
    if (remainder?.length) {
        sections.push({ title: FALLBACK_CATEGORY, connectors: remainder });
    }

    return sections;
};
