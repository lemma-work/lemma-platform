import { describe, expect, it } from 'vitest';

import {
    MAX_DIRECTORY_PAGES,
    ancestorFolderPaths,
    fetchChildFolders,
    folderDisplayPath,
    normalizeFolderPath,
    type FileListPage,
    type ListDirectory,
} from './folder-picker';

function folder(name: string, path: string, id = path) {
    return { id, name, path, kind: 'FOLDER' };
}

function file(name: string, path: string, id = path) {
    return { id, name, path, kind: 'FILE' };
}

/** Records every directory asked for, so tests can assert the walk stays shallow. */
function recordingLister(pagesByDirectory: Record<string, FileListPage[]>) {
    const calls: Array<{ directoryPath?: string; pageToken?: string }> = [];
    const lister: ListDirectory = async ({ directoryPath, pageToken }) => {
        calls.push({ directoryPath, pageToken });
        const pages = pagesByDirectory[directoryPath ?? '<root>'] || [];
        const index = pageToken ? Number(pageToken) : 0;
        return pages[index] || { items: [] };
    };
    return { lister, calls };
}

describe('normalizeFolderPath', () => {
    it('anchors, collapses, and trims folder paths', () => {
        expect(normalizeFolderPath('reports')).toBe('/reports');
        expect(normalizeFolderPath('//reports//q3//')).toBe('/reports/q3');
        expect(normalizeFolderPath('/')).toBe('/');
        expect(normalizeFolderPath('  ')).toBe('/');
        expect(normalizeFolderPath(null)).toBeUndefined();
    });
});

describe('folderDisplayPath', () => {
    it('drops the leading slash', () => {
        expect(folderDisplayPath('/reports/q3')).toBe('reports/q3');
    });
});

describe('ancestorFolderPaths', () => {
    it('lists the directories on the way to a folder, excluding the folder itself', () => {
        expect(ancestorFolderPaths('/reports/2024/q3')).toEqual(['/reports', '/reports/2024']);
    });

    it('has no ancestors for a top-level folder', () => {
        expect(ancestorFolderPaths('/reports')).toEqual([]);
    });
});

describe('fetchChildFolders', () => {
    it('reads only the requested directory, never its subfolders', async () => {
        const { lister, calls } = recordingLister({
            '<root>': [{ items: [folder('reports', '/reports'), file('notes.md', '/notes.md')] }],
            '/reports': [{ items: [folder('q3', '/reports/q3')] }],
        });

        const level = await fetchChildFolders(lister, '/');

        expect(level.folders).toEqual([{ id: '/reports', name: 'reports', path: '/reports' }]);
        expect(level.truncated).toBe(false);
        expect(calls).toEqual([{ directoryPath: undefined, pageToken: undefined }]);
    });

    it('sorts by name and skips the personal root', async () => {
        const { lister } = recordingLister({
            '<root>': [{
                items: [folder('zebra', '/zebra'), folder('me', '/me'), folder('alpha', '/alpha')],
            }],
        });

        const level = await fetchChildFolders(lister, '/');

        expect(level.folders.map((entry) => entry.name)).toEqual(['alpha', 'zebra']);
    });

    it('follows pages within one directory until they run out', async () => {
        const { lister, calls } = recordingLister({
            '/reports': [
                { items: [file('a.md', '/reports/a.md')], next_page_token: '1' },
                { items: [folder('q3', '/reports/q3')] },
            ],
        });

        const level = await fetchChildFolders(lister, '/reports');

        expect(level.folders.map((entry) => entry.path)).toEqual(['/reports/q3']);
        expect(level.truncated).toBe(false);
        expect(calls).toHaveLength(2);
    });

    it('stops at the page cap and reports the level as truncated', async () => {
        const pages: FileListPage[] = Array.from({ length: MAX_DIRECTORY_PAGES + 2 }, (_, index) => ({
            items: [file(`f${index}.md`, `/big/f${index}.md`)],
            next_page_token: String(index + 1),
        }));
        const { lister, calls } = recordingLister({ '/big': pages });

        const level = await fetchChildFolders(lister, '/big');

        expect(calls).toHaveLength(MAX_DIRECTORY_PAGES);
        expect(level.truncated).toBe(true);
    });

    it('derives a path when the listing omits one', async () => {
        const { lister } = recordingLister({
            '/reports': [{ items: [{ id: 'abc', name: 'q3', kind: 'FOLDER' }] }],
        });

        const level = await fetchChildFolders(lister, '/reports');

        expect(level.folders).toEqual([{ id: 'abc', name: 'q3', path: '/reports/q3' }]);
    });
});
