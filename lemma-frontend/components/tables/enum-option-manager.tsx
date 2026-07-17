'use client';

import { useState } from 'react';
import { Plus, X } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { getEnumColorByIndex } from '@/lib/utils/enum-color-utils';

interface EnumOptionManagerProps {
    options: string[];
    onChange: (options: string[]) => void;
}

export function EnumOptionManager({ options, onChange }: EnumOptionManagerProps) {
    const [newOption, setNewOption] = useState('');

    const addOption = () => {
        const trimmed = newOption.trim();
        if (!trimmed || options.includes(trimmed)) {
            return;
        }

        onChange([...options, trimmed]);
        setNewOption('');
    };

    const removeOption = (index: number) => {
        onChange(options.filter((_, optionIndex) => optionIndex !== index));
    };

    return (
        <div className="space-y-2">
            {options.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                    {options.map((option, index) => {
                        const color = getEnumColorByIndex(index);

                        return (
                            <div
                                key={`${option}-${index}`}
                                className={`group inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium shadow-[var(--shadow-xs)] ${color.bg} ${color.text} ${color.border}`}
                            >
                                <span>{option}</span>
                                <button
                                    type="button"
                                    className="enum-option-remove-button flex h-3.5 w-3.5 items-center justify-center rounded-full transition-gentle hover:bg-[color:color-mix(in_srgb,_var(--surface-1)_50%,_transparent)]"
                                    onClick={() => removeOption(index)}
                                >
                                    <X className="h-2.5 w-2.5" />
                                </button>
                            </div>
                        );
                    })}
                </div>
            ) : null}

            <div className="flex gap-1.5">
                <input
                    type="text"
                    value={newOption}
                    className="enum-option-field flex-1 rounded-md border border-[color:var(--row-border)] bg-[var(--surface-1)] px-2.5 py-1.5 text-sm text-[var(--text-primary)] transition-gentle placeholder:text-[var(--text-tertiary)] hover:border-[color:var(--border-strong)] focus-ring"
                    onChange={(event) => setNewOption(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key !== 'Enter') return;
                        event.preventDefault();
                        addOption();
                    }}
                    placeholder="Add option..."
                />
                <Button
                    type="button"
                    onClick={addOption}
                    disabled={!newOption.trim() || options.includes(newOption.trim())}
                    size="sm"
                    className="h-8 w-8 p-0"
                >
                    <Plus className="h-4 w-4" />
                </Button>
            </div>

            {options.length === 0 ? <p className="text-xs text-[var(--text-tertiary)]">Add at least one option</p> : null}
        </div>
    );
}
