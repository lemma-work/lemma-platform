import { describe, expect, it } from 'vitest';

import {
    sandboxImageNotice,
    shouldKeepPolling,
    type SandboxImageStatus,
} from '@/lib/desktop/sandbox-images';

const status = (
    state: SandboxImageStatus['state'],
    detail = '',
): SandboxImageStatus => ({ state, detail });

describe('sandboxImageNotice', () => {
    it('announces the download when it starts', () => {
        const notice = sandboxImageNotice('pending', status('downloading', 'Downloading the image pods run their work in'));

        expect(notice.kind).toBe('downloading');
        expect(notice).toMatchObject({
            description: 'Downloading the image pods run their work in',
        });
    });

    it('says nothing to a workspace that was already warm', () => {
        // The common case for the rest of an install's life. A "sandbox ready"
        // toast here would fire on every single reload.
        expect(sandboxImageNotice(null, status('ready')).kind).toBe('none');
    });

    it('reports the ending only to whoever saw the beginning', () => {
        expect(sandboxImageNotice('downloading', status('ready')).kind).toBe('ready');
        expect(sandboxImageNotice('pending', status('ready')).kind).toBe('none');
    });

    it('treats a failed download as information, not an error', () => {
        // Lemma still works. The image is fetched on first use, which is what
        // happened before any of this existed.
        const notice = sandboxImageNotice('downloading', status('failed'));

        expect(notice.kind).toBe('unavailable');
    });

    it('repeats nothing while the state stands still', () => {
        expect(sandboxImageNotice('downloading', status('downloading')).kind).toBe(
            'none',
        );
    });

    it('stays quiet about a state it does not recognise', () => {
        expect(sandboxImageNotice('downloading', status('unknown')).kind).toBe('none');
    });
});

describe('shouldKeepPolling', () => {
    it('keeps asking while the answer can still change', () => {
        expect(shouldKeepPolling(null)).toBe(true);
        expect(shouldKeepPolling('pending')).toBe(true);
        expect(shouldKeepPolling('downloading')).toBe(true);
    });

    it('stops once the download has an ending', () => {
        expect(shouldKeepPolling('ready')).toBe(false);
        expect(shouldKeepPolling('failed')).toBe(false);
    });
});

describe('an unreported state', () => {
    it('is not mistaken for an ending', () => {
        // The shell answers `pending` until locald reports, precisely so this
        // never happens — but a state this file does not recognise must not
        // silently look like a finished download either.
        expect(sandboxImageNotice(null, status('unknown')).kind).toBe('none');
        expect(shouldKeepPolling('unknown')).toBe(false);
    });

    it('keeps asking while the shell says pending', () => {
        expect(sandboxImageNotice(null, status('pending')).kind).toBe('none');
        expect(shouldKeepPolling('pending')).toBe(true);
    });
});
