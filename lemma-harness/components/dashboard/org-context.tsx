'use client';

import { usePathname } from 'next/navigation';

import { activeOrganizationIdFrom } from '@/lib/organizations/active-organization';
import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { useOrganizations } from '@/lib/hooks/use-organizations';
import { useLemmaAuth } from '@/lib/hooks/use-lemma-auth';
import type { Organization } from '@/lib/types';

interface OrganizationContextType {
    currentOrg: Organization | null;
    setCurrentOrg: (org: Organization) => void;
    organizations: Organization[];
    isLoading: boolean;
    hasSession: boolean;
}

const OrganizationContext = createContext<OrganizationContextType | undefined>(undefined);
const ORG_STORAGE_KEY = 'lemma:selected-org-id';

function getStoredOrgId() {
    if (typeof window === 'undefined') {
        return null;
    }

    return window.localStorage.getItem(ORG_STORAGE_KEY);
}

export function OrganizationProvider({ children }: { children: React.ReactNode }) {
    const { isAuthenticated, isLoading } = useLemmaAuth();
    const hasSession = isAuthenticated;
    const { data: orgsData, isLoading: isLoadingOrganizations } = useOrganizations({ enabled: hasSession });
    const organizations = useMemo(() => orgsData?.items || [], [orgsData?.items]);
    const [currentOrgId, setCurrentOrgId] = useState<string | null>(() => getStoredOrgId());
    const pathname = usePathname();
    const routeOrganizationId = activeOrganizationIdFrom(pathname);

    // A route that names an organization is a stronger statement about which
    // one the user is working in than whatever localStorage remembers. Without
    // this, following a direct link to organization A's settings left "current"
    // on B -- and `CreatePodScreen` sends `currentOrg.id`, so a pod created
    // from that page was filed under the wrong organization.
    //
    // Derived rather than copied into state by an effect: the effect version
    // rendered one frame with the old organization before correcting itself,
    // and cascaded a re-render on every navigation.
    //
    // Guarded on membership: an id in the URL the user cannot reach must not
    // become the organization the rest of the app acts on.
    const effectiveOrgId =
        routeOrganizationId && organizations.some((org) => org.id === routeOrganizationId)
            ? routeOrganizationId
            : currentOrgId;

    useEffect(() => {
        if (typeof window === 'undefined') {
            return;
        }

        if (!hasSession) {
            window.localStorage.removeItem(ORG_STORAGE_KEY);
            return;
        }

        if (organizations.length === 0) {
            window.localStorage.removeItem(ORG_STORAGE_KEY);
            return;
        }

        if (!effectiveOrgId) {
            return;
        }

        const orgStillExists = organizations.some((org) => org.id === effectiveOrgId);
        if (orgStillExists) {
            window.localStorage.setItem(ORG_STORAGE_KEY, effectiveOrgId);
            return;
        }

        window.localStorage.removeItem(ORG_STORAGE_KEY);
    }, [effectiveOrgId, hasSession, organizations]);

    const currentOrg = useMemo(() => {
        if (!hasSession) return null;
        if (organizations.length === 0) return null;
        if (!effectiveOrgId) return organizations[0];

        return organizations.find((org) => org.id === effectiveOrgId) || organizations[0];
    }, [hasSession, organizations, effectiveOrgId]);

    const setCurrentOrg = (org: Organization) => {
        if (typeof window !== 'undefined') {
            window.localStorage.setItem(ORG_STORAGE_KEY, org.id);
        }
        setCurrentOrgId(org.id);
    };

    const isContextLoading = isLoading || (hasSession && isLoadingOrganizations);

    return (
        <OrganizationContext.Provider value={{ currentOrg, setCurrentOrg, organizations, isLoading: isContextLoading, hasSession }}>
            {children}
        </OrganizationContext.Provider>
    );
}

export function useOrganization() {
    const context = useContext(OrganizationContext);
    if (context === undefined) {
        throw new Error('useOrganization must be used within an OrganizationProvider');
    }
    return context;
}
