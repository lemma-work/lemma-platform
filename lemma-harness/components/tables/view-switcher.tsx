'use client';

import { LayoutGrid, List } from '@/components/ui/icons';

export type ViewType = 'grid' | 'list' | 'kanban' | 'calendar';

interface ViewSwitcherProps {
    currentView: ViewType;
    onViewChange: (view: ViewType) => void;
}

const VIEWS = [
    { id: 'grid' as ViewType, label: 'Grid', icon: LayoutGrid },
    { id: 'list' as ViewType, label: 'List', icon: List },
];

export function ViewSwitcher({ currentView, onViewChange }: ViewSwitcherProps) {
    return (
        <div className="segmented-control">
            {VIEWS.map((view) => {
                const Icon = view.icon;
                const isActive = currentView === view.id;

                return (
                    <button
                        key={view.id}
                        type="button"
                        onClick={() => onViewChange(view.id)}
                        className="segmented-control-item segmented-control-item-icon"
                        data-active={isActive}
                        aria-pressed={isActive}
                        title={view.label}
                    >
                        <Icon className="w-3.5 h-3.5" />
                    </button>
                );
            })}
        </div>
    );
}
