import { describe, expect, it } from 'vitest';

import { carriesAccess, functionAccessChanged, type FunctionAccessFields } from './use-functions';
import { AccessMode, ConnectorMode, type Function as FunctionType } from '@/lib/types';

function wiredFunction(overrides: Partial<FunctionType> = {}): FunctionType {
    return {
        id: 'fn-1',
        pod_id: 'pod-1',
        user_id: 'user-1',
        name: 'summarize',
        description: null,
        icon_url: null,
        config: null,
        config_schema: null,
        code_path: null,
        code: null,
        input_schema: {},
        output_schema: {},
        accessible_tables: [{ table_name: 'tickets', mode: AccessMode.WRITE }],
        accessible_folders: [{ folder_path: '/runbooks', mode: AccessMode.READ }],
        accessible_connectors: [{ app_name: 'slack', mode: ConnectorMode.DYNAMIC }],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        ...overrides,
    } as FunctionType;
}

function savePayload(overrides: Partial<FunctionAccessFields> = {}): FunctionAccessFields {
    const fn = wiredFunction();
    return {
        accessible_tables: fn.accessible_tables,
        accessible_folders: fn.accessible_folders,
        accessible_connectors: fn.accessible_connectors,
        ...overrides,
    };
}

describe('functionAccessChanged', () => {
    // Same defect the agent editor had: editing only the code must not send the
    // save through the permissions endpoint, which pod editors cannot reach.
    it('is false for a save that leaves the wiring alone', () => {
        expect(functionAccessChanged(wiredFunction(), savePayload())).toBe(false);
    });

    it('ignores ordering differences between the server and the editor', () => {
        const fn = wiredFunction({
            accessible_folders: [
                { folder_path: '/runbooks', mode: AccessMode.READ },
                { folder_path: '/specs', mode: AccessMode.WRITE },
            ],
        });
        expect(functionAccessChanged(fn, savePayload({
            accessible_folders: [
                { folder_path: '/specs', mode: AccessMode.WRITE },
                { folder_path: '/runbooks', mode: AccessMode.READ },
            ],
        }))).toBe(false);
    });

    it('is true when a folder is added', () => {
        expect(functionAccessChanged(wiredFunction(), savePayload({
            accessible_folders: [
                { folder_path: '/runbooks', mode: AccessMode.READ },
                { folder_path: '/specs', mode: AccessMode.WRITE },
            ],
        }))).toBe(true);
    });

    it('is true when a folder stays but its mode widens', () => {
        expect(functionAccessChanged(wiredFunction(), savePayload({
            accessible_folders: [{ folder_path: '/runbooks', mode: AccessMode.WRITE }],
        }))).toBe(true);
    });

    it('is true when the wiring is cleared outright', () => {
        expect(functionAccessChanged(wiredFunction(), savePayload({
            accessible_tables: [],
            accessible_folders: [],
            accessible_connectors: [],
        }))).toBe(true);
    });

    it('treats an untouched field as unchanged rather than as a clear', () => {
        expect(functionAccessChanged(wiredFunction(), {})).toBe(false);
    });
});

describe('carriesAccess', () => {
    it('is false for a payload that never mentions access', () => {
        expect(carriesAccess({})).toBe(false);
    });

    it('is true once any access field is present, including an empty one', () => {
        expect(carriesAccess({ accessible_folders: [] })).toBe(true);
    });
});
