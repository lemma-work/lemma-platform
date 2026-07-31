import { describe, expect, it } from 'vitest';

import { barOwnsTitle, resolveTabLabel } from './topbar-title';

describe('barOwnsTitle', () => {
    it('keeps the bar title on for routes that do not claim it', () => {
        expect(barOwnsTitle(undefined, false)).toBe(true);
        expect(barOwnsTitle('bar', false)).toBe(true);
    });

    it('ignores hero visibility when the bar owns the title', () => {
        // A stale `true` left by a previous route must not blank a bar-owned
        // title — hence the owner check comes first.
        expect(barOwnsTitle(undefined, true)).toBe(true);
        expect(barOwnsTitle('bar', true)).toBe(true);
    });

    it('cedes the title while the page heading is on screen', () => {
        expect(barOwnsTitle('page', true)).toBe(false);
    });

    it('takes the title back once the page heading is gone', () => {
        // Covers both scrolling past the heading and landing on a tab that has
        // none — an editor pane unmounts the heading, which reports false.
        expect(barOwnsTitle('page', false)).toBe(true);
    });

    it('cedes the title while the workspace tab strip names the resource', () => {
        expect(barOwnsTitle('tab', false, true)).toBe(false);
    });

    it('takes the title back when the tab strip is not showing it', () => {
        // Compact viewports hide the strip, and some routes have no tab of
        // their own. Without this, nothing on screen would name the resource.
        expect(barOwnsTitle('tab', false, false)).toBe(true);
    });

    it('keeps tab ownership independent of hero visibility', () => {
        // A stale heroTitleVisible from a previous route must not leak across
        // into a tab-owned route's decision.
        expect(barOwnsTitle('tab', true, true)).toBe(false);
        expect(barOwnsTitle('tab', true, false)).toBe(true);
    });

    it('ignores the tab strip for page- and bar-owned routes', () => {
        expect(barOwnsTitle('page', true, true)).toBe(false);
        expect(barOwnsTitle('page', false, true)).toBe(true);
        expect(barOwnsTitle('bar', false, true)).toBe(true);
        expect(barOwnsTitle(undefined, false, true)).toBe(true);
    });
});

describe('resolveTabLabel', () => {
    it('prefers an explicit tab title', () => {
        expect(resolveTabLabel('Agents', 'Suggestions Agent')).toBe('Agents');
    });

    it('falls back to a plain-string title', () => {
        expect(resolveTabLabel(undefined, 'Suggestions Agent')).toBe('Suggestions Agent');
    });

    it('trims whitespace from either source', () => {
        expect(resolveTabLabel('  Agents  ', undefined)).toBe('Agents');
        expect(resolveTabLabel(undefined, '  Suggestions Agent  ')).toBe('Suggestions Agent');
    });

    it('yields an empty label for non-string titles', () => {
        // Routes such as the data browser pass an interactive element as their
        // title; there is no text to lift, so the caller keeps its own label.
        expect(resolveTabLabel(undefined, { type: 'div' })).toBe('');
        expect(resolveTabLabel(undefined, undefined)).toBe('');
        expect(resolveTabLabel(undefined, null)).toBe('');
    });

    it('lets an explicit tab title rescue a non-string title', () => {
        expect(resolveTabLabel('Data', { type: 'div' })).toBe('Data');
    });

    it('prefers an explicit empty tab title over the title', () => {
        // `??` not `||`: passing '' is a deliberate "no tab label", and must not
        // silently fall through to the bar title.
        expect(resolveTabLabel('', 'Suggestions Agent')).toBe('');
    });
});
