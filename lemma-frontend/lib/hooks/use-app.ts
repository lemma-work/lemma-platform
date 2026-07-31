'use client';

import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';
import type { AppConfig, AppPage, AppPageRef } from '../types/app';
import { createUniqueAppPageSlug, normalizeAppPageSlug } from '../utils/app-page-slugs';

export interface AppListItem {
    id: string;
    name: string;
    url: string;
    description?: string | null;
    public_slug?: string;
    status?: string;
    visibility?: string | null;
    allowed_actions?: string[] | null;
}

interface AppPageQueryOptions {
    mode?: 'editor' | 'view';
}

type AppIndexItem = Record<string, unknown>;

export const appIndexQueryKey = (podId: string) => ['apps', 'index', podId] as const;

async function listAppIndex(podId: string): Promise<AppIndexItem[]> {
    const response = await getLemmaClient(podId).apps.list({ limit: 1000 }) as { items?: unknown[] };
    return Array.isArray(response?.items)
        ? response.items.map((item) => (item || {}) as AppIndexItem)
        : [];
}

function toOptionalString(value: unknown): string | undefined {
    return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined;
}

function normalizeAppUrl(value: string | undefined): string | undefined {
    if (!value) return undefined;
    if (/^https?:\/\//i.test(value)) return value;
    if (value.startsWith('//')) return `https:${value}`;
    return `https://${value}`;
}

function normalizeAllowedActions(value: unknown): string[] | null {
    if (!Array.isArray(value)) return null;
    return value.filter((action): action is string => typeof action === 'string' && action.trim().length > 0);
}

export async function listAppPageRefs(podId: string): Promise<AppPageRef[]> {
    return appPageRefsFromIndex(await listAppIndex(podId));
}

function appPageRefsFromIndex(items: AppIndexItem[]): AppPageRef[] {
    const existingSlugs: string[] = [];

    return items
        .map((item, index) => {
            const pageName = toOptionalString(item.name);
            if (!pageName) return null;
            const slug = createUniqueAppPageSlug({
                title: pageName,
                preferredSlug: pageName,
                existingSlugs,
            });
            existingSlugs.push(slug);
            return {
                id: toOptionalString(item.id),
                slug,
                title: pageName,
                appName: pageName,
                description: typeof item.description === 'string' ? item.description.trim() || null : null,
                url: normalizeAppUrl(toOptionalString(item.url)),
                order: index,
                path: `pages/${slug}.json`,
                visibility: typeof item.visibility === 'string' ? item.visibility : null,
                allowed_actions: normalizeAllowedActions(item.allowed_actions),
            } as AppPageRef;
        })
        .filter((item): item is AppPageRef => item !== null);
}

export async function listApps(podId: string): Promise<AppListItem[]> {
    return appsFromIndex(await listAppIndex(podId));
}

function appsFromIndex(items: AppIndexItem[]): AppListItem[] {
    const apps: AppListItem[] = [];

    for (const item of items) {
        const id = toOptionalString(item.id);
        const name = toOptionalString(item.name);
        const url = normalizeAppUrl(toOptionalString(item.url));

        if (!id || !name || !url) continue;

        apps.push({
            id,
            name,
            url,
            description: typeof item.description === 'string' ? item.description : null,
            public_slug: toOptionalString(item.public_slug),
            status: toOptionalString(item.status),
            visibility: typeof item.visibility === 'string' ? item.visibility : null,
            allowed_actions: normalizeAllowedActions(item.allowed_actions),
        });
    }

    return apps;
}

export function useAppConfig(podId: string) {
    return useQuery({
        queryKey: appIndexQueryKey(podId),
        queryFn: () => listAppIndex(podId),
        select: (items): AppConfig | null => {
            const pages = appPageRefsFromIndex(items);
            if (pages.length === 0) return null;
            const now = new Date().toISOString();
            return {
                id: `app-index-${podId}`,
                podId,
                name: 'Default App',
                pages,
                createdAt: now,
                updatedAt: now,
            };
        },
        enabled: !!podId,
        staleTime: 2 * 60 * 1000,
        gcTime: 30 * 60 * 1000,
    });
}

// Get list of app pages
export function useAppPages(podId: string) {
    const { data: config, isLoading, error, refetch } = useAppConfig(podId);

    const pages = useMemo(() => {
        if (!config?.pages) return [];
        return [...config.pages].sort((a: AppPageRef, b: AppPageRef) => a.order - b.order);
    }, [config]);

    return { pages, isLoading, error, revalidate: refetch };
}

export function useApps(podId: string) {
    return useQuery({
        queryKey: appIndexQueryKey(podId),
        queryFn: () => listAppIndex(podId),
        select: appsFromIndex,
        enabled: !!podId,
        staleTime: 2 * 60 * 1000,
        gcTime: 30 * 60 * 1000,
    });
}

export function useDeleteApp() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, name }: { podId: string; name: string }) =>
            getLemmaClient(podId).apps.delete(name),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: appIndexQueryKey(variables.podId) });
            queryClient.invalidateQueries({ queryKey: ['app-page', variables.podId] });
        },
    });
}

export function useUpdateAppVisibility() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({
            podId,
            name,
            visibility,
        }: {
            podId: string;
            name: string;
            visibility: string;
        }) => getLemmaClient(podId).apps.update(name, { visibility }),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: appIndexQueryKey(variables.podId) });
            queryClient.invalidateQueries({ queryKey: ['app-page', variables.podId] });
        },
    });
}

// Get a single app page
export function useAppPage(
    podId: string,
    pageSlug: string | null,
    pageRef?: AppPageRef | null,
    options?: AppPageQueryOptions
) {
    const mode = options?.mode || 'view';
    const queryClient = useQueryClient();

    return useQuery({
        queryKey: ['app-page', podId, pageRef?.slug || pageSlug, mode],
        queryFn: async (): Promise<AppPage> => {
            const index = await queryClient.ensureQueryData({
                queryKey: appIndexQueryKey(podId),
                queryFn: () => listAppIndex(podId),
                staleTime: 2 * 60 * 1000,
            });
            const refs = appPageRefsFromIndex(index);
            const targetRef = pageRef
                ? refs.find((entry) => entry.slug === pageRef.slug) || pageRef
                : refs.find((entry) => entry.slug === normalizeAppPageSlug(pageSlug || ''));
            if (!targetRef) {
                throw new Error('Page not found');
            }

            const pageName = targetRef.title;
            const metadataRaw = await getLemmaClient(podId).apps.get(pageName).catch(() => null);
            const metadata = metadataRaw && typeof metadataRaw === 'object'
                ? metadataRaw as Record<string, unknown>
                : {};
            const url = normalizeAppUrl(toOptionalString(metadata.url) || targetRef.url);
            const createdAt = toOptionalString(metadata.created_at) || new Date().toISOString();
            const updatedAt = toOptionalString(metadata.updated_at) || new Date().toISOString();

            return {
                id: toOptionalString(metadata.id) || targetRef.id,
                slug: targetRef.slug,
                podId,
                title: toOptionalString(metadata.name) || targetRef.title,
                url,
                icon: undefined,
                order: targetRef.order,
                createdAt,
                updatedAt,
                visibility: typeof metadata.visibility === 'string' ? metadata.visibility : targetRef.visibility,
                allowed_actions: normalizeAllowedActions(metadata.allowed_actions) || targetRef.allowed_actions,
            };
        },
        enabled: !!podId && !!pageSlug,
        staleTime: mode === 'view' ? 5 * 60 * 1000 : 0,
        refetchOnWindowFocus: mode !== 'view',
        refetchOnReconnect: mode !== 'view',
        gcTime: mode === 'view' ? 30 * 60 * 1000 : undefined,
    });
}
