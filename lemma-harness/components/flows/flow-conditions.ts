import { DECISION_OPERATORS } from './flow-editor-constants';
import type {
    ConditionBuilder,
    ConditionOperand,
    ConditionOperandMode,
    ConditionOperator,
    StepBranch,
} from './flow-editor-types';

export function createConditionOperand(mode: ConditionOperandMode = 'path'): ConditionOperand {
    return {
        mode,
        path: '',
        value: '',
        literalType: 'string',
    };
}

export function createConditionBuilder(): ConditionBuilder {
    return {
        left: createConditionOperand('path'),
        operator: '==',
        right: createConditionOperand('value'),
    };
}

export function parseLiteralValue(text: string): ConditionOperand | null {
    const trimmed = text.trim();

    if (trimmed === '') {
        return {
            mode: 'value',
            value: '',
            path: '',
            literalType: 'string',
        };
    }

    if (/^(True|False)$/i.test(trimmed)) {
        return {
            mode: 'value',
            path: '',
            value: /^true$/i.test(trimmed) ? 'true' : 'false',
            literalType: 'boolean',
        };
    }

    if (/^(None|null)$/i.test(trimmed)) {
        return {
            mode: 'value',
            path: '',
            value: '',
            literalType: 'null',
        };
    }

    if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
        return {
            mode: 'value',
            path: '',
            value: trimmed,
            literalType: 'number',
        };
    }

    if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith('\'') && trimmed.endsWith('\''))) {
        if (trimmed.startsWith('"')) {
            try {
                return {
                    mode: 'value',
                    path: '',
                    value: JSON.parse(trimmed),
                    literalType: 'string',
                };
            } catch {
                // Keep fallback parsing below.
            }
        }
        const raw = trimmed.slice(1, -1).replace(/\\'/g, '\'').replace(/\\"/g, '"');
        return {
            mode: 'value',
            path: '',
            value: raw,
            literalType: 'string',
        };
    }

    return null;
}

export function parseOperand(text: string): ConditionOperand {
    const literal = parseLiteralValue(text);
    if (literal) return literal;

    return {
        mode: 'path',
        path: text.trim(),
        value: '',
        literalType: 'string',
    };
}

export function parseConditionBuilder(condition: string): ConditionBuilder | null {
    const match = condition.match(/^\s*(.+?)\s+(not in|==|!=|>=|<=|>|<|in)\s+(.+?)\s*$/);
    if (!match) return null;

    const [, leftRaw, operatorRaw, rightRaw] = match;
    if (!DECISION_OPERATORS.includes(operatorRaw as ConditionOperator)) return null;

    return {
        left: parseOperand(leftRaw),
        operator: operatorRaw as ConditionOperator,
        right: parseOperand(rightRaw),
    };
}

export function serializeOperand(operand: ConditionOperand): string {
    if (operand.mode === 'path') {
        return operand.path.trim() || '""';
    }

    if (operand.literalType === 'number') {
        const parsed = Number(operand.value);
        return Number.isFinite(parsed) ? String(parsed) : '0';
    }

    if (operand.literalType === 'boolean') {
        return operand.value === 'true' ? 'True' : 'False';
    }

    if (operand.literalType === 'null') {
        return 'None';
    }

    return JSON.stringify(operand.value ?? '');
}

export function buildConditionExpression(builder: ConditionBuilder): string {
    return `${serializeOperand(builder.left)} ${builder.operator} ${serializeOperand(builder.right)}`;
}

export function getBranchCondition(branch: StepBranch): string {
    if (branch.conditionBuilder) {
        return buildConditionExpression(branch.conditionBuilder);
    }
    return branch.condition || '1 == 1';
}
