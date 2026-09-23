'use client';

import { useEffect } from 'react';
import { logoutToHome } from '@/lib/auth/logout';

export default function LogoutPage() {
    useEffect(() => {
        void logoutToHome();
    }, []);

    return (
        <main className="grid min-h-screen place-items-center bg-[var(--background)] px-6 text-center text-[var(--text-secondary)]">
            <p className="text-sm font-semibold">Signing you out...</p>
        </main>
    );
}
