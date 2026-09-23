import type { DatastoreFile } from '@/lib/types';
import type { FileSearchResultSchema } from 'lemma-sdk';

export const FILES_TAB_VALUE = '__files__';

export type FileNamespaceMode = 'PERSONAL' | 'POD';
export type DatastoreSearchResult = FileSearchResultSchema;

export type SearchResultItem = {
    path: string;
    fileId: string;
    fileName: string;
    snippet: string;
    score: number;
    chunkIndex: number;
};

export function hasOwnProperty(value: object, key: string): boolean {
    return Object.prototype.hasOwnProperty.call(value, key);
}

export function isFolder(file: DatastoreFile): boolean {
    return file.kind === 'FOLDER';
}

export function joinPath(basePath: string | null | undefined, segment: string): string {
    const cleanSegment = segment.trim().replace(/^\/+|\/+$/g, '');
    if (!cleanSegment) {
        const normalizedBase = (basePath || '/').trim();
        return normalizedBase || '/';
    }

    const normalizedBase = (basePath || '/').trim() || '/';
    if (normalizedBase === '/') return `/${cleanSegment}`;
    return `${normalizedBase.replace(/\/+$/, '')}/${cleanSegment}`;
}

export function getFileNameFromPath(path: string): string {
    const normalized = path.replace(/\\/g, '/');
    const parts = normalized.split('/').filter(Boolean);
    return parts[parts.length - 1] || normalized;
}

export function getDatastoreFilePath(file: DatastoreFile): string {
    return file.path || file.id;
}

export function getParentPath(path: string | null | undefined): string | null {
    const normalized = (path || '').trim();
    if (!normalized || normalized === '/') return null;

    const withoutTrailingSlash = normalized.replace(/\/+$/, '');
    const slashIndex = withoutTrailingSlash.lastIndexOf('/');
    if (slashIndex <= 0) return null;

    return withoutTrailingSlash.slice(0, slashIndex);
}

export function normalizeFileNamespace(value: unknown): FileNamespaceMode {
    return String(value || '').toUpperCase() === 'POD' ? 'POD' : 'PERSONAL';
}

export function getFileNamespaceLabel(namespace: FileNamespaceMode): string {
    return namespace === 'PERSONAL' ? 'Personal files' : 'Pod files';
}

export function getFileVisibility(file: DatastoreFile, fallbackNamespace: FileNamespaceMode): string {
    return file.visibility || fallbackNamespace;
}

export function isImageFile(file: DatastoreFile): boolean {
    const mimeType = (file.mime_type || '').toLowerCase();
    if (mimeType.startsWith('image/')) return true;

    const name = file.name.toLowerCase();
    return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.avif', '.ico'].some((ext) =>
        name.endsWith(ext)
    );
}
