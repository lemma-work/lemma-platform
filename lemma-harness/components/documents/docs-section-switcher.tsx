'use client';

import { Table, Sparkles, UserRound } from '@/components/ui/icons';

import { DOC_SECTIONS, type DocSectionId } from '@/lib/files/doc-sections';

const SECTION_ICONS = {
    POD: Table,
    SKILLS: Sparkles,
    PERSONAL: UserRound,
} as const;

const SECTION_HINTS: Record<DocSectionId, string> = {
    POD: 'Shared with the pod',
    SKILLS: 'Procedures your agents can load',
    PERSONAL: 'Private to you',
};

/**
 * Three doors, always in the same place. The alternative — `/skills` and `/me`
 * sitting as folder rows among ordinary pod docs — hides the two sections that
 * behave least like a folder inside the one control that says "folder".
 */
export function DocsSectionSwitcher({
    activeSection,
    onSectionChange,
}: {
    activeSection: DocSectionId;
    onSectionChange: (section: DocSectionId) => void;
}) {
    return (
        <div className="segmented-control" role="tablist" aria-label="Docs sections">
            {DOC_SECTIONS.map((section) => {
                const Icon = SECTION_ICONS[section.id];
                const active = activeSection === section.id;
                return (
                    <button
                        key={section.id}
                        type="button"
                        role="tab"
                        onClick={() => onSectionChange(section.id)}
                        className="segmented-control-item"
                        data-active={active}
                        aria-selected={active}
                        title={SECTION_HINTS[section.id]}
                    >
                        <Icon className="h-3.5 w-3.5" />
                        <span>{section.label}</span>
                    </button>
                );
            })}
        </div>
    );
}
