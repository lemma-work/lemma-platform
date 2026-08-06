import { describe, expect, it } from 'vitest';

import {
    describeAutosaveStatus,
    isAutosavedPreviewType,
    shouldShowSaveButton,
} from './document-save-state';

describe('isAutosavedPreviewType', () => {
    it('autosaves prose', () => {
        expect(isAutosavedPreviewType('markdown')).toBe(true);
    });

    it('leaves source files to the writer', () => {
        // The exact bytes are the point in these, so a timer must not decide
        // when a half-typed one lands.
        expect(isAutosavedPreviewType('json')).toBe(false);
        expect(isAutosavedPreviewType('code')).toBe(false);
        expect(isAutosavedPreviewType('html')).toBe(false);
    });

    it('does not claim types it has never seen', () => {
        expect(isAutosavedPreviewType('pdf')).toBe(false);
        expect(isAutosavedPreviewType('')).toBe(false);
    });
});

describe('describeAutosaveStatus', () => {
    it('says nothing about a document that was only read', () => {
        expect(describeAutosaveStatus({ state: 'idle', hasUnsavedChanges: false })).toBeNull();
    });

    it('reports a failure over everything else', () => {
        expect(describeAutosaveStatus({ state: 'error', hasUnsavedChanges: true })).toEqual({
            label: "Couldn't save",
            tone: 'error',
        });
    });

    it('shows the write in flight', () => {
        // Still dirty while the request is out — the in-flight state wins, or
        // every save would flicker through "Unsaved changes" on its way to
        // "Saved".
        expect(describeAutosaveStatus({ state: 'saving', hasUnsavedChanges: true })).toEqual({
            label: 'Saving…',
            tone: 'quiet',
        });
    });

    it('admits to edits that have not landed yet', () => {
        expect(describeAutosaveStatus({ state: 'idle', hasUnsavedChanges: true })).toEqual({
            label: 'Unsaved changes',
            tone: 'quiet',
        });
    });

    it('does not keep claiming "Saved" once you type again', () => {
        expect(describeAutosaveStatus({ state: 'saved', hasUnsavedChanges: true })?.label).toBe(
            'Unsaved changes'
        );
        expect(describeAutosaveStatus({ state: 'saved', hasUnsavedChanges: false })?.label).toBe(
            'Saved'
        );
    });
});

describe('shouldShowSaveButton', () => {
    it('appears for source files with pending edits', () => {
        expect(shouldShowSaveButton({ previewType: 'json', canWrite: true, hasUnsavedChanges: true })).toBe(true);
    });

    it('stays away from documents that save themselves', () => {
        expect(shouldShowSaveButton({ previewType: 'markdown', canWrite: true, hasUnsavedChanges: true })).toBe(false);
    });

    it('stays away when there is nothing to save', () => {
        expect(shouldShowSaveButton({ previewType: 'json', canWrite: true, hasUnsavedChanges: false })).toBe(false);
    });

    it('stays away from a reader who cannot write', () => {
        expect(shouldShowSaveButton({ previewType: 'json', canWrite: false, hasUnsavedChanges: true })).toBe(false);
    });
});
