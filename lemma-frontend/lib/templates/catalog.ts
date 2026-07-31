export interface PublicTemplate {
    slug: string;
    name: string;
    category: string;
    kicker: string;
    description: string;
    github: string;
    outcomes: string[];
    includes: Array<{
        label: string;
        detail: string;
    }>;
}

export const PUBLIC_TEMPLATES: PublicTemplate[] = [
    {
        slug: 'roundtable',
        name: 'Roundtable',
        category: 'Team operations',
        kicker: 'Real work, shared context, human judgment',
        description:
            'A shared task board where people and agents can pick up work, leave a clear trail, and hand it back.',
        github: 'https://github.com/deepak-jha-kgp/roundtable',
        outcomes: [
            'Assign the same issue to a teammate or a named agent',
            'Keep questions, progress, files, and final work in one thread',
            'Continue agent work without starting over in a fresh chat',
        ],
        includes: [
            {
                label: 'Shared task board',
                detail: 'Projects and issues with the context, files, instructions, owners, and status needed to do the work.',
            },
            {
                label: 'Named agents',
                detail: 'A researcher, writer, squad leader, and default worker with distinct responsibilities.',
            },
            {
                label: 'Assignment workflows',
                detail: 'Starts work when an issue is assigned and resumes it when someone adds a comment.',
            },
            {
                label: 'Durable work history',
                detail: 'Keeps issues, comments, attachments, events, and saved deliverables together.',
            },
        ],
    },
    {
        slug: 'panini',
        name: 'Panini',
        category: 'Writing',
        kicker: 'AI proposes, people decide, the document remembers',
        description:
            'A writing space where agents comment on the exact passage, suggest an edit, and wait for you to decide.',
        github: 'https://github.com/deepak-jha-kgp/panini',
        outcomes: [
            'Ask a writer, critic, or researcher to review the current draft',
            'Keep comments and proposed edits anchored to the passage they concern',
            'Accept or reject individual proposals without replacing the whole document',
        ],
        includes: [
            {
                label: 'Document editor',
                detail: 'A focused writing space with versions, presence, comments, and edit locks.',
            },
            {
                label: 'Review agents',
                detail: 'A writer, critic, and researcher with separate jobs and review perspectives.',
            },
            {
                label: 'Inline proposals',
                detail: 'Checked operations for comments, proposed changes, and applying only approved edits.',
            },
            {
                label: 'Review workflow',
                detail: 'Routes a document review to the right agent while keeping the decision with its author.',
            },
        ],
    },
    {
        slug: 'frontdesk',
        name: 'Frontdesk',
        category: 'Customer support',
        kicker: 'Every request routed, every answer grounded, every send controlled',
        description:
            'A shared support inbox that sorts new messages, prepares an answer, and leaves the final send with a person.',
        github: 'https://github.com/deepak-jha-kgp/frontdesk',
        outcomes: [
            'Bring support messages, customer context, and ownership into one queue',
            'Prepare cited replies from your playbooks and routing rules',
            'Require a person to review sensitive answers before they are sent',
        ],
        includes: [
            {
                label: 'Support inbox',
                detail: 'A shared conversation list with customers, messages, priorities, drafts, and response deadlines.',
            },
            {
                label: 'Desk agent',
                detail: 'Sorts incoming requests and drafts grounded answers within explicit sending boundaries.',
            },
            {
                label: 'Team playbooks',
                detail: 'Keeps routing rules, response-time policies, and the source material behind each answer.',
            },
            {
                label: 'Guarded sending',
                detail: 'Checked functions and workflows separate preparing a response from sending it.',
            },
        ],
    },
    {
        slug: 'smart-inbox',
        name: 'Smart Inbox',
        category: 'Personal productivity',
        kicker: 'Your rules, your voice, a quieter inbox',
        description:
            'A personal inbox that follows the rules you choose and prepares replies without sending behind your back.',
        github: 'https://github.com/deepak-jha-kgp/smart-inbox',
        outcomes: [
            'Sort Gmail threads using tags and instructions you define',
            'Summarize long conversations and prepare replies in your voice',
            'Keep every draft separate from the send action until you approve it',
        ],
        includes: [
            {
                label: 'Calm inbox',
                detail: 'A focused view of email threads, tags, summaries, drafts, and personal preferences.',
            },
            {
                label: 'Inbox agent',
                detail: 'Sorts, summarizes, and drafts according to the rules and writing style you choose.',
            },
            {
                label: 'Gmail connection',
                detail: 'Brings your own account into the pod without bundling mail or credentials in the template.',
            },
            {
                label: 'Send boundary',
                detail: 'Uses a guarded function so preparation can be automatic while sending remains deliberate.',
            },
        ],
    },
    {
        slug: 'sidekick',
        name: 'Sidekick',
        category: 'Personal productivity',
        kicker: 'Knows the routine, remembers the context, asks before acting',
        description:
            'A personal assistant that remembers how you work, handles recurring tasks, and asks before it acts outside the pod.',
        github: 'https://github.com/deepak-jha-kgp/sidekick',
        outcomes: [
            'Keep tasks, routines, preferences, and useful memory in one place',
            'Turn repeated behavior into a routine instead of another prompt',
            'Review actions for connected tools before the assistant executes them',
        ],
        includes: [
            {
                label: 'Assistant home',
                detail: 'A personal workspace for tasks, routines, memory, inbox items, skills, and proposed actions.',
            },
            {
                label: 'Working agent',
                detail: 'Handles tasks inside the pod using the context and instructions you have made durable.',
            },
            {
                label: 'Memory agent',
                detail: 'Distills completed work into compact context that can improve the next task.',
            },
            {
                label: 'Approval boundary',
                detail: 'Prepares connected-tool actions for review before anything happens outside the pod.',
            },
        ],
    },
    {
        slug: 'lemma-design',
        name: 'Lemma Design',
        category: 'Design',
        kicker: 'Design the idea, prototype the feeling, make it real',
        description:
            'A place to turn a brief into several live prototypes, try them, and keep revising the direction that works.',
        github: 'https://github.com/deepak-jha-kgp/lemma-design',
        outcomes: [
            'Create several genuinely distinct directions from one brief',
            'Open and use each direction as a working HTML prototype',
            'Give focused feedback and keep revising the direction that works',
        ],
        includes: [
            {
                label: 'Design studio',
                detail: 'A live workspace for briefs, project files, directions, comments, feedback, and generation history.',
            },
            {
                label: 'Design agents',
                detail: 'A planner chooses the directions, a renderer creates them, and a reviser handles focused feedback.',
            },
            {
                label: 'Working prototypes',
                detail: 'Each direction is generated as an interface you can open and try, not a static mood board.',
            },
            {
                label: 'Project memory',
                detail: 'Keeps the brief, design system, decisions, files, and every generation run together.',
            },
        ],
    },
    {
        slug: 'nachiketa',
        name: 'Nachiketa',
        category: 'Learning',
        kicker: 'Start with a question, build from sources, learn by attempting',
        description:
            'A learning space that starts with your goal, researches the subject, and changes the path as you attempt the work.',
        github: 'https://github.com/deepak-jha-kgp/nachiketa',
        outcomes: [
            'Turn a learning goal into a sourced map of concepts and lessons',
            'Learn through concrete attempts instead of passively reading a fixed course',
            'Adjust the path when your evidence shows that a concept did not land',
        ],
        includes: [
            {
                label: 'Learning path',
                detail: 'A focused interface for goals, lessons, steps, attempts, sessions, and evidence of understanding.',
            },
            {
                label: 'Source records',
                detail: 'Keeps the research behind the material visible instead of hiding where each lesson came from.',
            },
            {
                label: 'Teaching agents',
                detail: 'A domain researcher, learning coach, and Socratic guide with separate responsibilities.',
            },
            {
                label: 'Adaptive workflow',
                detail: 'Expands or redirects the path when the current attempts call for more depth.',
            },
        ],
    },
    {
        slug: 'drop',
        name: 'Drop',
        category: 'Capture',
        kicker: 'Send it once, find it later, keep the context',
        description:
            'A Telegram capture box that cleans up what you send and makes it possible to find again.',
        github: 'https://github.com/deepak-jha-kgp/drop',
        outcomes: [
            'Capture links, notes, and other material quickly from Telegram',
            'Turn each item into a useful title, summary, tags, and filing information',
            'Browse and search the collection without losing the original context',
        ],
        includes: [
            {
                label: 'Telegram capture',
                detail: 'A low-friction front door for sending links, notes, and other material from your phone.',
            },
            {
                label: 'Curator agent',
                detail: 'Cleans up and files new captures while a retrieval agent helps find them again.',
            },
            {
                label: 'Searchable collection',
                detail: 'A quiet app for browsing the saved material, its summary, tags, and original note.',
            },
            {
                label: 'Capture trail',
                detail: 'Saves the original item before background work and records what happened afterward.',
            },
        ],
    },
    {
        slug: 'meal',
        name: 'Meal',
        category: 'Wellbeing',
        kicker: 'Say what you ate, see the pattern, choose one next move',
        description:
            'A food journal that accepts ordinary meal descriptions and turns them into a useful daily picture.',
        github: 'https://github.com/deepak-jha-kgp/meal',
        outcomes: [
            'Log a meal in Telegram using the words you would normally use',
            'Keep estimates honest with the original description and confidence signals',
            'See the day update after each meal and get one useful next move',
        ],
        includes: [
            {
                label: 'Telegram meal log',
                detail: 'A quick way to record meals without searching for exact ingredients, brands, or serving sizes.',
            },
            {
                label: 'Meal agent',
                detail: 'Interprets ordinary descriptions while preserving uncertainty instead of inventing precision.',
            },
            {
                label: 'Daily view',
                detail: 'Shows meals, nutrition totals, confidence, and corrections in one calm interface.',
            },
            {
                label: 'Review workflow',
                detail: 'Records each meal through checked writes and starts a review when new information arrives.',
            },
        ],
    },
    {
        slug: 'lemma-gtm',
        name: 'Lemma GTM',
        category: 'Go to market',
        kicker: 'Position clearly, build with proof, ship the campaign',
        description:
            'A campaign home that keeps the audience, argument, proof, assets, and decisions in one readable place.',
        github: 'https://github.com/deepak-jha-kgp/lemma-gtm',
        outcomes: [
            'Frame a campaign around a specific audience and problem',
            'Keep positioning, narrative, proof, and assets connected',
            'See what is ready, what changed, and what still needs a person',
        ],
        includes: [
            {
                label: 'Campaign home',
                detail: 'A readable workspace for the audience, promise, narrative, proof, assets, and open decisions.',
            },
            {
                label: 'Packaged app',
                detail: 'A focused first version that installs as a complete campaign surface in your pod.',
            },
            {
                label: 'Editable design',
                detail: 'Includes the app source and design decisions so the campaign experience can become your own.',
            },
        ],
    },
];

export function getPublicTemplateBySlug(slug: string | null | undefined): PublicTemplate | null {
    return PUBLIC_TEMPLATES.find((template) => template.slug === slug) ?? null;
}

export function templateRunHref(template: PublicTemplate): string {
    const source = new URL(template.github);
    if (source.hostname.toLowerCase() !== 'github.com') {
        throw new Error(`Template "${template.slug}" must use a github.com source.`);
    }
    const [owner, repoWithSuffix] = source.pathname.split('/').filter(Boolean);
    const repo = repoWithSuffix?.replace(/\.git$/i, '');
    if (!owner || !repo) {
        throw new Error(`Template "${template.slug}" has an invalid GitHub source.`);
    }
    return `/import/github/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
}

export function templateCoverPath(template: PublicTemplate): string {
    return `/templates/${encodeURIComponent(template.slug)}/social-preview.jpg`;
}
