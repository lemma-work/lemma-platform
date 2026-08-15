'use client';

import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useState,
    type ReactNode,
} from 'react';
import { usePathname } from 'next/navigation';
import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { podIdFromPathname } from '@/lib/pods/pod-id-from-pathname';

/**
 * PodLayoutProvider is the single source of truth for the pod shell's three
 * regions (nav, assistant, surface). It replaces the booleans that used to be
 * scattered across PodShell + the assistant context, so every toggle maps to one
 * region and the docked-vs-overlay decision is made in one place.
 *
 * Region presentation is *derived* from a small amount of intent
 * (nav open/closed, assistant open/closed) plus the measured environment
 * (viewport class + shell width). Components read presentation; they never
 * recompute it.
 */

export type LayoutViewport = 'compact' | 'medium' | 'wide';
export type NavPresentation = 'expanded' | 'rail' | 'drawer';
// On desktop the assistant always docks in-flow alongside the surface; the
// surface reflows to its actual width via container queries rather than the
// assistant floating over content. Overlay is reserved for compact viewports,
// where there is simply no room to split.
export type AssistantPresentation = 'closed' | 'docked' | 'overlay';

export const ASSISTANT_DOCK_WIDTHS = [520, 560, 640, 720] as const;
export type AssistantDockWidth = (typeof ASSISTANT_DOCK_WIDTHS)[number];
export const DEFAULT_ASSISTANT_DOCK_WIDTH: AssistantDockWidth = 640;

const COMPACT_MAX_PX = 767;
const MEDIUM_MAX_PX = 1199;

export const ASSISTANT_DOCK_WIDTH_CLASS: Record<AssistantDockWidth, string> = {
    520: 'md:w-[520px]',
    560: 'md:w-[560px]',
    640: 'md:w-[640px]',
    720: 'md:w-[720px]',
};

export function closestAssistantDockWidth(value: number): AssistantDockWidth {
    return ASSISTANT_DOCK_WIDTHS.reduce(
        (closest, width) =>
            Math.abs(width - value) < Math.abs(closest - value) ? width : closest,
        ASSISTANT_DOCK_WIDTHS[0],
    );
}

interface PodLayoutContextValue {
    viewport: LayoutViewport;
    isCompact: boolean;
    // Nav region
    navPresentation: NavPresentation;
    isNavOpen: boolean; // desktop expanded state
    isMobileNavOpen: boolean;
    toggleNav: () => void;
    openNav: () => void;
    closeNav: () => void;
    setMobileNavOpen: (open: boolean) => void;
    // Assistant region
    assistantPresentation: AssistantPresentation;
    isAssistantOpen: boolean;
    isFocusRoute: boolean;
    assistantDockWidth: AssistantDockWidth;
    setAssistantDockWidth: (width: AssistantDockWidth) => void;
    closeAssistant: () => void;
}

const PodLayoutContext = createContext<PodLayoutContextValue | null>(null);


/**
 * Focus routes are full-task surfaces (resource editors and run inspectors) that
 * need the whole width and have their own chrome. The assistant does not dock
 * beside them — it steps back to a launcher. Detail = a section with an id-ish
 * second segment (incl. "new"); the bare section list stays a companion route.
 */
function isFocusRoute(pathname: string): boolean {
    const podId = podIdFromPathname(pathname);
    if (!podId) return false;
    const base = `/pod/${podId}`;
    const parts = pathname.slice(base.length).split('/').filter(Boolean);
    const [section, detail] = parts;
    if (!section || !detail) return false;
    return (
        section === 'ai' ||
        section === 'agents' ||
        section === 'flows' ||
        section === 'functions'
    );
}

/**
 * The assistant may dock on every route except pod home (its launch surface,
 * where it is force-closed) and focus routes (full-task editors/inspectors,
 * where it yields to a launcher).
 */
function isAssistantAllowedRoute(pathname: string): boolean {
    const podId = podIdFromPathname(pathname);
    if (!podId) return false;
    const base = `/pod/${podId}`;
    if (pathname === base || pathname === `${base}/`) return false;
    return !isFocusRoute(pathname);
}

export function PodLayoutProvider({ children }: { children: ReactNode }) {
    const pathname = usePathname();
    const { isOpen: isAssistantOpenRaw, closeAssistant: closeAssistantRaw } = useAIAssistant();

    const [isNavOpen, setIsNavOpen] = useState(true);
    const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
    const [assistantDockWidth, setAssistantDockWidth] = useState<AssistantDockWidth>(
        DEFAULT_ASSISTANT_DOCK_WIDTH,
    );
    const [viewport, setViewport] = useState<LayoutViewport>('wide');

    // Viewport class — drives drawer vs rail/expanded and compact behaviour.
    useEffect(() => {
        const compactQuery = window.matchMedia(`(max-width: ${COMPACT_MAX_PX}px)`);
        const mediumQuery = window.matchMedia(`(max-width: ${MEDIUM_MAX_PX}px)`);
        const sync = () => {
            const next: LayoutViewport = compactQuery.matches
                ? 'compact'
                : mediumQuery.matches
                    ? 'medium'
                    : 'wide';
            setViewport(next);
            // Leaving compact closes the off-canvas drawer so it can't reappear
            // pinned once the inline nav takes over.
            if (next !== 'compact') setIsMobileNavOpen(false);
        };
        sync();
        compactQuery.addEventListener('change', sync);
        mediumQuery.addEventListener('change', sync);
        return () => {
            compactQuery.removeEventListener('change', sync);
            mediumQuery.removeEventListener('change', sync);
        };
    }, []);

    const isCompact = viewport === 'compact';
    const focusRoute = isFocusRoute(pathname);
    const assistantAllowed = isAssistantAllowedRoute(pathname);
    const isAssistantOpen = isAssistantOpenRaw && assistantAllowed;

    const navPresentation: NavPresentation = isCompact
        ? 'drawer'
        : isNavOpen
            ? 'expanded'
            : 'rail';

    const assistantPresentation: AssistantPresentation = !isAssistantOpen
        ? 'closed'
        : isCompact
            ? 'overlay'
            : 'docked';

    const toggleNav = useCallback(() => {
        if (window.matchMedia(`(max-width: ${COMPACT_MAX_PX}px)`).matches) {
            setIsMobileNavOpen((current) => !current);
        } else {
            setIsNavOpen((current) => !current);
        }
    }, []);

    const openNav = useCallback(() => setIsNavOpen(true), []);
    const closeNav = useCallback(() => setIsNavOpen(false), []);
    const setMobileNavOpen = useCallback((open: boolean) => setIsMobileNavOpen(open), []);
    const closeAssistant = useCallback(() => closeAssistantRaw(), [closeAssistantRaw]);

    const value = useMemo<PodLayoutContextValue>(() => ({
        viewport,
        isCompact,
        navPresentation,
        isNavOpen,
        isMobileNavOpen,
        toggleNav,
        openNav,
        closeNav,
        setMobileNavOpen,
        assistantPresentation,
        isAssistantOpen,
        isFocusRoute: focusRoute,
        assistantDockWidth,
        setAssistantDockWidth,
        closeAssistant,
    }), [
        assistantDockWidth,
        assistantPresentation,
        closeAssistant,
        closeNav,
        focusRoute,
        isAssistantOpen,
        isCompact,
        isMobileNavOpen,
        isNavOpen,
        navPresentation,
        openNav,
        setMobileNavOpen,
        toggleNav,
        viewport,
    ]);

    return <PodLayoutContext.Provider value={value}>{children}</PodLayoutContext.Provider>;
}

export function usePodLayout() {
    const context = useContext(PodLayoutContext);
    if (!context) {
        throw new Error('usePodLayout must be used within a PodLayoutProvider');
    }
    return context;
}

/** Non-throwing variant for components that may render outside the pod shell. */
export function usePodLayoutOptional() {
    return useContext(PodLayoutContext);
}
