'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

export function PodSettingsNav({ podId }: { podId: string }) {
    const pathname = usePathname();
    const items = [
        {
            label: 'General',
            description: 'Pod defaults and runtime behavior.',
            href: `/pod/${podId}/settings`,
        },
        {
            label: 'Access',
            description: 'Who can enter the pod and what role they hold.',
            href: `/pod/${podId}/settings/members`,
        },
        {
            label: 'Automation',
            description: 'Every trigger in the pod, and what it wakes up.',
            href: `/pod/${podId}/settings/automation`,
        },
        {
            label: 'Usage',
            description: 'Spend, limits, and model activity for this pod.',
            href: `/pod/${podId}/settings/usage`,
        },
    ];

    return (
        <nav className="lemma-header-tabs">
            {items.map((item) => {
                const active = item.href.endsWith('/settings')
                    ? pathname === item.href
                    : pathname === item.href || pathname.startsWith(`${item.href}/`);

                return (
                    <Link
                        key={item.label}
                        href={item.href}
                        className="lemma-header-tab inline-flex items-center"
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
