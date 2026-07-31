'use client';

import { X } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';

interface ExpressionBuilderProps {
    availableFields: string[];
    value: string;
    onChange: (expression: string) => void;
}

const OPERATORS = [
    { value: ' + ', label: '+', title: 'Add' },
    { value: ' - ', label: '-', title: 'Subtract' },
    { value: ' * ', label: '*', title: 'Multiply' },
    { value: ' / ', label: '/', title: 'Divide' },
    { value: ' % ', label: '%', title: 'Modulo' },
    { value: ' || ', label: '||', title: 'Concatenate' },
];

const SQL_FUNCTIONS = [
    { value: 'SUM', label: 'SUM', title: 'Sum of values' },
    { value: 'AVG', label: 'AVG', title: 'Average' },
    { value: 'COUNT', label: 'COUNT', title: 'Count rows' },
    { value: 'MIN', label: 'MIN', title: 'Minimum value' },
    { value: 'MAX', label: 'MAX', title: 'Maximum value' },
    { value: 'UPPER', label: 'UPPER', title: 'Convert to uppercase' },
    { value: 'LOWER', label: 'LOWER', title: 'Convert to lowercase' },
    { value: 'LENGTH', label: 'LENGTH', title: 'String length' },
    { value: 'CONCAT', label: 'CONCAT', title: 'Concatenate strings' },
    { value: 'ROUND', label: 'ROUND', title: 'Round number' },
    { value: 'ABS', label: 'ABS', title: 'Absolute value' },
    { value: 'COALESCE', label: 'COALESCE', title: 'Return first non-null' },
];

const SYSTEM_FIELDS = [
    { value: 'created_at', label: 'Created At', icon: 'CT' },
    { value: 'updated_at', label: 'Updated At', icon: 'UP' },
];

export function ExpressionBuilder({ availableFields, value, onChange }: ExpressionBuilderProps) {
    const insertValue = (nextValue: string) => {
        onChange(value + nextValue);
    };

    const clear = () => {
        onChange('');
    };

    const fieldButtonClass =
        'rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] px-1.5 py-0.5 text-xs font-medium text-[var(--chip-fg)] transition-gentle hover:border-[color:var(--field-border-hover)] hover:bg-[var(--row-bg-hover)]';
    const operatorButtonClass =
        'flex h-6 w-6 items-center justify-center rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] text-xs font-semibold text-[var(--chip-fg)] transition-gentle hover:border-[color:var(--field-border-hover)] hover:bg-[var(--row-bg-hover)]';
    const functionButtonClass =
        'rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] px-1.5 py-0.5 text-xs font-medium text-[var(--chip-fg)] transition-gentle hover:border-[color:var(--field-border-hover)] hover:bg-[var(--row-bg-hover)]';

    return (
        <div className="surface-panel-muted space-y-2 p-2.5">
            <div className="relative">
                <textarea
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    placeholder="Build expression..."
                    rows={2}
                    className="expression-builder-field min-h-[48px] w-full resize-y rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] p-2 font-mono text-xs text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[color:var(--field-border-hover)] focus-ring"
                />
                {value ? (
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={clear}
                        className="hover-state-error absolute right-1 top-1 h-5 w-5 rounded text-[var(--text-tertiary)] hover:text-[var(--state-error)]"
                    >
                        <X className="h-3 w-3" />
                    </Button>
                ) : null}
            </div>

            <div className="space-y-2">
                <div>
                    <label className="mb-1 block type-eyebrow">
                        Fields
                    </label>
                    <div className="flex flex-wrap gap-1">
                        {availableFields.map((field) => (
                            <button
                                key={field}
                                type="button"
                                onClick={() => insertValue(field)}
                                className={`expression-builder-chip-button ${fieldButtonClass}`}
                            >
                                {field}
                            </button>
                        ))}
                        {SYSTEM_FIELDS.map((field) => (
                            <button
                                key={field.value}
                                type="button"
                                onClick={() => insertValue(field.value)}
                                className={`expression-builder-chip-button ${fieldButtonClass} font-mono`}
                                title={field.label}
                            >
                                {field.icon} {field.value}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                    <div>
                        <label className="mb-1 block type-eyebrow">
                            Operators
                        </label>
                        <div className="flex flex-wrap gap-1">
                            {OPERATORS.map((operator) => (
                                <button
                                    key={operator.value}
                                    type="button"
                                    onClick={() => insertValue(operator.value)}
                                    className={`expression-builder-operator-button ${operatorButtonClass}`}
                                    title={operator.title}
                                >
                                    {operator.label}
                                </button>
                            ))}
                            <button type="button" onClick={() => insertValue('(')} className={`expression-builder-operator-button ${operatorButtonClass}`}>
                                (
                            </button>
                            <button type="button" onClick={() => insertValue(')')} className={`expression-builder-operator-button ${operatorButtonClass}`}>
                                )
                            </button>
                            <button type="button" onClick={() => insertValue(', ')} className={`expression-builder-operator-button ${operatorButtonClass}`}>
                                ,
                            </button>
                        </div>
                    </div>

                    <div>
                        <label className="mb-1 block type-eyebrow">
                            Manual
                        </label>
                        <input
                            type="text"
                            placeholder="Type and press Enter"
                            onKeyDown={(event) => {
                                if (event.key !== 'Enter') return;
                                event.preventDefault();
                                const input = event.currentTarget;
                                if (!input.value) return;
                                insertValue(input.value);
                                input.value = '';
                            }}
                            className="expression-builder-field w-full rounded-md border border-[color:var(--field-border)] bg-[var(--field-bg)] px-2 py-1 text-xs text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[color:var(--field-border-hover)] focus-ring"
                        />
                    </div>
                </div>

                <div>
                    <label className="mb-1 block type-eyebrow">
                        Functions
                    </label>
                    <div className="flex max-h-[48px] flex-wrap gap-1 overflow-y-auto">
                        {SQL_FUNCTIONS.map((func) => (
                            <button
                                key={func.value}
                                type="button"
                                onClick={() => insertValue(`${func.value}()`)}
                                className={`expression-builder-chip-button ${functionButtonClass}`}
                                title={func.title}
                            >
                                {func.label}
                            </button>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
