export interface TourStep {
    /** CSS selector for the anchor element, always a [data-edu="..."] attribute. */
    anchor: string;
    title: string;
    body: string;
}

export interface TourDefinition {
    id: 'agent-editor' | 'flow-editor';
    steps: TourStep[];
}

/**
 * Anchors are an explicit contract: every selector here must match a
 * data-edu attribute in the codebase (checked by scripts/check-edu-anchors.mjs).
 * A step whose anchor is missing at runtime is skipped silently.
 */
export const TOURS: Record<TourDefinition['id'], TourDefinition> = {
    'agent-editor': {
        id: 'agent-editor',
        steps: [
            {
                anchor: '[data-edu="agent-instructions"]',
                title: 'Instructions are the job description',
                body: 'Write what arrives, what the agent should produce, and what to do when unsure. A worked example beats a paragraph of adjectives.',
            },
            {
                anchor: '[data-edu="agent-variables"]',
                title: 'Variables make it composable',
                body: 'Declare the inputs the agent takes and the fields it returns. That contract is what lets a workflow or app call it and rely on the result — leave it empty for plain chat.',
            },
            {
                anchor: '[data-edu="agent-runtime"]',
                title: 'Pick the brain',
                body: 'Judgment-heavy work wants the strongest model; high-volume triage wants a fast one. Leave the pod default until real runs tell you otherwise.',
            },
            {
                anchor: '[data-edu="agent-access"]',
                title: 'Scope what it can touch',
                body: 'Grant only the tables, folders, and apps this job needs. Start from nothing and add — a narrow agent is a trustworthy agent.',
            },
            {
                anchor: '[data-edu="agent-sharing"]',
                title: 'Decide who can use it',
                body: 'Sharing controls people: keep it private while you draft, open it to the pod when it works. This is separate from what the agent itself can touch.',
            },
        ],
    },
    'flow-editor': {
        id: 'flow-editor',
        steps: [
            {
                anchor: '[data-edu="flow-start"]',
                title: 'Every run begins here',
                body: 'The start decides when this workflow fires: by hand, on a schedule, from a webhook, or when data changes. Click it to change the trigger.',
            },
            {
                anchor: '[data-edu="flow-add-step"]',
                title: 'Add steps in order',
                body: 'Each step does one job. Mix agent steps for judgment, function steps for exact rules, and approval gates before anything consequential.',
            },
            {
                anchor: '[data-edu="flow-step-detail"]',
                title: 'Configure the selected step',
                body: 'Click any step to set its work type, inputs, and branching. The Work Type field is where a step becomes an agent, a function, or a human gate.',
            },
            {
                anchor: '[data-edu="flow-view-toggle"]',
                title: 'Two views, one workflow',
                body: 'The Steps view is a checklist; the Flow view is the same process as a canvas. Runs land in their own tab so you can replay any execution step by step.',
            },
        ],
    },
};
