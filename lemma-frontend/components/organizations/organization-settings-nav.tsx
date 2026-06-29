'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

export function OrganizationSettingsNav({ organizationId }: { organizationId: string }) {
    const pathname = usePathname();
    const items = [
        {
            label: 'Members',
            href: `/organizations/${organizationId}/settings/members`,
            description: 'People, roles, and invitations.',
        },
        {
            label: 'Models',
            href: `/organizations/${organizationId}/settings/agent-runtimes`,
            description: 'Built-in models, your provider keys, and coding agents.',
        },
        {
            label: 'Usage',
            href: `/organizations/${organizationId}/settings/usage`,
            description: 'Spend, limits, and recent API activity.',
        },
    ];

    return (
        <nav className="lemma-header-tabs">
            {items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(`${item.href}/`);

                return (
                    <Link
                        key={item.href}
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
