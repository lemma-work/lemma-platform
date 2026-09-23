import type { Metadata } from "next";
import { ProtectedRoute } from '@/components/auth/protected-route';

export const metadata: Metadata = {
    title: "Workspace",
    description: "Manage AI pods, agents, workflows, schedules, apps, and records in Lemma.",
    robots: {
        index: false,
        follow: false,
        nocache: true,
        googleBot: {
            index: false,
            follow: false,
            noimageindex: true,
        },
    },
};

export default function DashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <ProtectedRoute>
            <div className="min-h-screen bg-transparent text-[var(--text-primary)]">
                {children}
            </div>
        </ProtectedRoute>
    );
}
