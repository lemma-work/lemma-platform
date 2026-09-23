'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Bell } from '@/components/ui/icons';
import { useUnreadNotificationCount } from '@/lib/hooks/use-notifications';
import { buildNotificationHref } from '@/lib/notifications/notification-display';

type NotificationsBellProps = {
    podId: string | undefined;
};

/**
 * The bell. It navigates, and that is all it does.
 *
 * It used to open the whole inbox in a 22rem popover — every row printing its
 * title, its entire body, a meta line and up to three buttons — so six
 * notifications filled a column nothing could be scanned in. A peek that has to
 * be that big is not a peek; it is the page, drawn badly. The page is at
 * `/pod/{id}/notifications`.
 */
export function NotificationsBell({ podId }: NotificationsBellProps) {
    const pathname = usePathname();
    const { data: unread = 0 } = useUnreadNotificationCount(podId);

    if (!podId) return null;

    const href = buildNotificationHref(podId);
    const active = pathname === href;

    return (
        <Link
            href={href}
            data-active={active ? 'true' : undefined}
            className="lemma-shell-icon-button custom-focus-ring relative flex h-8 w-8 shrink-0 items-center justify-center self-center text-[var(--text-tertiary)] data-[active=true]:text-[var(--text-primary)]"
            aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
            title="Notifications"
        >
            <Bell className="h-4 w-4" strokeWidth={1.8} />
            {/* `--action-primary`, not the `--accent-9` this used to name: that
                token is defined in neither theme, so the fill resolved to
                nothing and the count was white-on-cream in light mode. */}
            {unread > 0 ? (
                <span aria-hidden className="notification-bell-badge">
                    {unread > 9 ? '9+' : unread}
                </span>
            ) : null}
        </Link>
    );
}
