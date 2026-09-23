import { NodeType, FlowDefinition } from '@/lib/types';

export interface FlowTemplate {
    id: string;
    name: string;
    description: string;
    icon: string;
    color: string;
    category: 'automation' | 'approval' | 'data' | 'ai' | 'notification';
    definition: FlowDefinition;
    estimatedTime: string;
    complexity: 'simple' | 'medium' | 'advanced';
}

const endNode = (id = 'done', label = 'Done') => ({
    id,
    type: NodeType.END,
    config: {},
    label,
});

export const flowTemplates: FlowTemplate[] = [
    {
        id: 'blank',
        name: 'Blank workflow',
        description: 'Start with an empty workflow and add each step yourself.',
        icon: 'Plus',
        color: 'neutral',
        category: 'automation',
        estimatedTime: 'Open-ended',
        complexity: 'simple',
        definition: {
            nodes: [],
            edges: [],
        },
    },
    {
        id: 'intake',
        name: 'Intake request',
        description: 'Collect the details needed before the work begins.',
        icon: 'ClipboardList',
        color: 'teal',
        category: 'automation',
        estimatedTime: '2 min',
        complexity: 'simple',
        definition: {
            nodes: [
                {
                    id: 'collect_request',
                    type: NodeType.FORM,
                    label: 'Collect request',
                    config: {
                        input_schema: {
                            type: 'object',
                            properties: {
                                title: { type: 'string', description: 'What needs to happen?' },
                                context: { type: 'string', description: 'Useful background' },
                            },
                            required: ['title'],
                        },
                    },
                },
                endNode(),
            ],
            edges: [
                { id: 'edge-1', source: 'collect_request', target: 'done' },
            ],
        },
    },
    {
        id: 'review-loop',
        name: 'Review loop',
        description: 'Collect work, pause for human review, then finish cleanly.',
        icon: 'ClipboardCheck',
        color: 'blue',
        category: 'approval',
        estimatedTime: '4 min',
        complexity: 'simple',
        definition: {
            nodes: [
                {
                    id: 'submit_work',
                    type: NodeType.FORM,
                    label: 'Submit work',
                    config: {
                        input_schema: {
                            type: 'object',
                            properties: {
                                summary: { type: 'string', description: 'What should be reviewed?' },
                                link: { type: 'string', description: 'Optional supporting link' },
                            },
                            required: ['summary'],
                        },
                    },
                },
                {
                    id: 'review_notes',
                    type: NodeType.FORM,
                    label: 'Review notes',
                    config: {
                        input_schema: {
                            type: 'object',
                            properties: {
                                decision: { type: 'string', enum: ['Approve', 'Needs changes'] },
                                notes: { type: 'string', description: 'Reviewer notes' },
                            },
                            required: ['decision'],
                        },
                    },
                },
                endNode(),
            ],
            edges: [
                { id: 'edge-1', source: 'submit_work', target: 'review_notes' },
                { id: 'edge-2', source: 'review_notes', target: 'done' },
            ],
        },
    },
    {
        id: 'approval-gate',
        name: 'Approval gate',
        description: 'Route a request to approved or needs-work outcomes.',
        icon: 'GitPullRequest',
        color: 'amber',
        category: 'approval',
        estimatedTime: '5 min',
        complexity: 'medium',
        definition: {
            nodes: [
                {
                    id: 'request_details',
                    type: NodeType.FORM,
                    label: 'Request details',
                    config: {
                        input_schema: {
                            type: 'object',
                            properties: {
                                request: { type: 'string', description: 'Request details' },
                                amount: { type: 'number', description: 'Amount or effort estimate' },
                            },
                            required: ['request'],
                        },
                    },
                },
                {
                    id: 'approval_check',
                    type: NodeType.DECISION,
                    label: 'Approval check',
                    config: {
                        rules: [
                            { condition: 'request_details.amount <= 1000', next_node_id: 'approved' },
                            { condition: '1 == 1', next_node_id: 'needs_work' },
                        ],
                    },
                },
                endNode('approved', 'Approved'),
                endNode('needs_work', 'Needs work'),
            ],
            edges: [
                { id: 'edge-1', source: 'request_details', target: 'approval_check' },
                { id: 'edge-2', source: 'approval_check', target: 'approved', label: 'Approved' },
                { id: 'edge-3', source: 'approval_check', target: 'needs_work', label: 'Needs work' },
            ],
        },
    },
    {
        id: 'data-check',
        name: 'Data check',
        description: 'Capture record context and confirm what changed.',
        icon: 'Database',
        color: 'green',
        category: 'data',
        estimatedTime: '3 min',
        complexity: 'simple',
        definition: {
            nodes: [
                {
                    id: 'record_context',
                    type: NodeType.FORM,
                    label: 'Record context',
                    config: {
                        input_schema: {
                            type: 'object',
                            properties: {
                                record_id: { type: 'string', description: 'Record or item id' },
                                current_state: { type: 'string', description: 'What is true now?' },
                            },
                            required: ['record_id'],
                        },
                    },
                },
                {
                    id: 'confirm_update',
                    type: NodeType.FORM,
                    label: 'Confirm update',
                    config: {
                        input_schema: {
                            type: 'object',
                            properties: {
                                update_summary: { type: 'string', description: 'What changed?' },
                                ready: { type: 'boolean', description: 'Ready to close?' },
                            },
                            required: ['update_summary'],
                        },
                    },
                },
                endNode(),
            ],
            edges: [
                { id: 'edge-1', source: 'record_context', target: 'confirm_update' },
                { id: 'edge-2', source: 'confirm_update', target: 'done' },
            ],
        },
    },
    {
        id: 'wait-follow-up',
        name: 'Wait and follow up',
        description: 'Collect context, wait briefly, then return to the work.',
        icon: 'Clock',
        color: 'rose',
        category: 'notification',
        estimatedTime: '3 min',
        complexity: 'simple',
        definition: {
            nodes: [
                {
                    id: 'follow_up_context',
                    type: NodeType.FORM,
                    label: 'Follow-up context',
                    config: {
                        input_schema: {
                            type: 'object',
                            properties: {
                                topic: { type: 'string', description: 'What should be followed up?' },
                                owner: { type: 'string', description: 'Who owns the next move?' },
                            },
                            required: ['topic'],
                        },
                    },
                },
                {
                    id: 'wait',
                    type: NodeType.WAIT_UNTIL,
                    label: 'Wait',
                    config: {
                        timeout_seconds: 300,
                    },
                },
                endNode(),
            ],
            edges: [
                { id: 'edge-1', source: 'follow_up_context', target: 'wait' },
                { id: 'edge-2', source: 'wait', target: 'done' },
            ],
        },
    },
];

export const getTemplateById = (id: string): FlowTemplate | undefined => {
    return flowTemplates.find((template) => template.id === id);
};

export const getTemplatesByCategory = (category: FlowTemplate['category']) => {
    return flowTemplates.filter((template) => template.category === category);
};
