import {
    Bot,
    Calendar,
    Code2,
    Database,
    Flag,
    GitBranch,
    Play,
    Repeat,
    Timer,
    UserRound,
    Webhook,
} from '@/components/ui/icons';

import { FlowStartType, NodeType } from '@/lib/types';
import type { ConditionOperator, StepType } from './flow-editor-types';

export const STEP_TYPES: StepType[] = [
    NodeType.FORM,
    NodeType.AGENT,
    NodeType.FUNCTION,
    NodeType.DECISION,
    NodeType.LOOP,
    NodeType.WAIT_UNTIL,
    NodeType.END,
];

export const STEP_TYPE_LABELS: Record<StepType, string> = {
    [NodeType.AGENT]: 'AI agent - run an agent',
    [NodeType.FUNCTION]: 'Function - run code',
    [NodeType.FORM]: 'Form - collect input',
    [NodeType.DECISION]: 'Decision - branch the process',
    [NodeType.LOOP]: 'Loop - repeat steps',
    [NodeType.WAIT_UNTIL]: 'Wait - pause until later',
    [NodeType.END]: 'End - finish the workflow',
};

export const STEP_TYPE_ICONS: Record<StepType, React.ComponentType<{ className?: string }>> = {
    [NodeType.AGENT]: Bot,
    [NodeType.FUNCTION]: Code2,
    [NodeType.FORM]: UserRound,
    [NodeType.DECISION]: GitBranch,
    [NodeType.LOOP]: Repeat,
    [NodeType.WAIT_UNTIL]: Timer,
    [NodeType.END]: Flag,
};

export const START_NODE_ID = '__workflow_start__';

export const START_TYPE_ICONS: Record<FlowStartType, React.ComponentType<{ className?: string }>> = {
    MANUAL: Play,
    SCHEDULED: Calendar,
    EVENT: Webhook,
    DATASTORE_EVENT: Database,
};

export const START_TYPE_LABELS: Record<FlowStartType, string> = {
    MANUAL: 'Manual start',
    SCHEDULED: 'Scheduled start',
    EVENT: 'Event start',
    DATASTORE_EVENT: 'Table start',
};

export const DECISION_OPERATORS: ConditionOperator[] = ['==', '!=', '>', '>=', '<', '<=', 'in', 'not in'];
