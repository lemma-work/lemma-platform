import { FlowNode, NodeType } from '@/lib/types';

export type StepType = Exclude<NodeType, NodeType.START>;

export type StepBranch = {
    id: string;
    label: string;
    condition: string;
    conditionBuilder?: ConditionBuilder;
    steps: StepNode[];
    /** An arm of the step's question rather than of a rule: the options that
     *  take it, and whether a question left open does. Saved into
     *  `config.question`, never as a rule or an edge. */
    question?: { answers: string[]; onOpen: boolean };
    /** The fall-through edge of a step that asks a question. Saved as an edge
     *  alone: a catch-all rule would answer before the question is asked. */
    fallThrough?: boolean;
};

export type StepNode = {
    id: string;
    type: StepType;
    label: string;
    config: Record<string, unknown>;
    inputs: Record<string, unknown>;
    branches?: StepBranch[];
    loopSteps?: StepNode[];
};

export type CanvasPosition = {
    x: number;
    y: number;
};

export type EditorSnapshot = {
    steps: StepNode[];
    positions: Record<string, CanvasPosition>;
    selectedStepId?: string;
};

export type EditorViewMode = 'steps' | 'flow';

export type ParseOptions = {
    nodeMap: Map<string, FlowNode>;
    outgoing: Map<string, string[]>;
    incoming: Map<string, number>;
    included: Set<string>;
    markIncluded: boolean;
    stopAtMerge: boolean;
    stopAtIds?: Set<string>;
};

export type ConditionOperator = '==' | '!=' | '>' | '>=' | '<' | '<=' | 'in' | 'not in';
export type ConditionOperandMode = 'path' | 'value';
export type ConditionLiteralType = 'string' | 'number' | 'boolean' | 'null';

export type ConditionOperand = {
    mode: ConditionOperandMode;
    path: string;
    value: string;
    literalType: ConditionLiteralType;
};

export type ConditionBuilder = {
    left: ConditionOperand;
    operator: ConditionOperator;
    right: ConditionOperand;
};

export type StepStats = {
    total: number;
    configured: number;
    branchCount: number;
};

export type InputFieldRequirement = {
    key: string;
    required: boolean;
    type?: string;
};

export type ExpressionBinding = {
    type: 'expression';
    value: string;
};

export type LiteralBinding = {
    type: 'literal';
    value: unknown;
};

export type EditorInputBinding = ExpressionBinding | LiteralBinding;
