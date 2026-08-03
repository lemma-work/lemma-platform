'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

const ITEMS = [
    {
        label: 'General',
        description: 'Pod defaults and runtime behavior.',
        segment: '',
    },
    {
        label: 'Access',
        description: 'Who can enter the pod and what role they hold.',
        segment: 'members',
    },
    {
        label: 'Automation',
        description: 'Every trigger in the pod, and what it wakes up.',
        segment: 'automation',
    },
    {
        label: 'Usage',
        description: 'Spend, limits, and model activity for this pod.',
        segment: 'usage',
    },
] as const;

/** The label the context bar prints for a settings route — see `PodSettingsShell`. */
export function getPodSettingsTitle(segment: (typeof ITEMS)[number]['segment']): string {
    return ITEMS.find((item) => item.segment === segment)?.label ?? 'Settings';
}

export function PodSettingsNav({ podId, className }: { podId: string; className?: string }) {
    const pathname = usePathname();
    const root = `/pod/${podId}/settings`;

    return (
        <nav className={cn('lemma-header-tabs', className)}>
            {ITEMS.map((item) => {
                const href = item.segment ? `${root}/${item.segment}` : root;
                // General is the index, so it only matches exactly; every other
                // tab also owns its sub-routes.
                const active = item.segment
                    ? pathname === href || pathname.startsWith(`${href}/`)
                    : pathname === root;

                return (
                    <Link
                        key={item.label}
                        href={href}
                        className="lemma-header-tab inline-flex shrink-0 items-center"
                        data-state={active ? 'active' : undefined}
                        aria-current={active ? 'page' : undefined}
                        title={item.description}
                    >
                        {item.label}
                    </Link>
                );
            })}
        </nav>
    );
}
