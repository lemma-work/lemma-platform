import { describe, expect, it } from 'vitest';

import {
    POD_FILTER_THRESHOLD,
    filterSwitcherPodGroups,
    filterSwitcherPods,
    matchesPodQuery,
    shouldShowPodFilter,
    toPodDisplayLabel,
} from './pod-switcher';

const pod = (id: string, name: string, organizationName?: string) => ({
    id,
    name,
    organization_name: organizationName,
});

describe('toPodDisplayLabel', () => {
    it('reads stored names as words', () => {
        expect(toPodDisplayLabel('inbox_crm')).toBe('Inbox Crm');
        expect(toPodDisplayLabel('morning-brief-desk')).toBe('Morning Brief Desk');
    });

    it('never renders an unnamed pod as a blank row', () => {
        expect(toPodDisplayLabel('')).toBe('Untitled');
        expect(toPodDisplayLabel(null)).toBe('Untitled');
        expect(toPodDisplayLabel('   ')).toBe('Untitled');
    });
});

describe('shouldShowPodFilter', () => {
    it('holds the field back while the list is still one glance', () => {
        expect(shouldShowPodFilter(POD_FILTER_THRESHOLD)).toBe(false);
        expect(shouldShowPodFilter(POD_FILTER_THRESHOLD + 1)).toBe(true);
        expect(shouldShowPodFilter(0)).toBe(false);
    });
});

describe('matchesPodQuery', () => {
    it('matches the label as displayed, not the stored name', () => {
        expect(matchesPodQuery(pod('1', 'inbox_crm'), 'inbox crm')).toBe(true);
        expect(matchesPodQuery(pod('1', 'morning-brief-desk'), 'brief desk')).toBe(true);
    });

    it('matches on the organisation a pod sits under', () => {
        expect(matchesPodQuery(pod('1', 'Odyssey', 'Gappy'), 'gappy')).toBe(true);
        expect(matchesPodQuery(pod('1', 'Odyssey'), 'gappy')).toBe(false);
    });

    it('ignores case and surrounding whitespace', () => {
        expect(matchesPodQuery(pod('1', 'Ledflex Support'), '  LEDFLEX ')).toBe(true);
    });

    it('keeps every pod while the query is empty', () => {
        expect(matchesPodQuery(pod('1', 'Odyssey'), '')).toBe(true);
        expect(matchesPodQuery(pod('1', 'Odyssey'), '   ')).toBe(true);
    });
});

describe('filterSwitcherPods', () => {
    const pods = [
        pod('1', 'inbox_crm', "deepak's personal"),
        pod('2', 'linkedin_manager', "deepak's personal"),
        pod('3', 'Odyssey', 'Gappy'),
    ];

    it('returns the list untouched when nothing was typed', () => {
        expect(filterSwitcherPods(pods, '')).toEqual(pods);
    });

    it('narrows to the pods that answer the query', () => {
        expect(filterSwitcherPods(pods, 'in').map((match) => match.id)).toEqual(['1', '2']);
        expect(filterSwitcherPods(pods, 'odys').map((match) => match.id)).toEqual(['3']);
    });

    it('can come back empty rather than falling back to everything', () => {
        expect(filterSwitcherPods(pods, 'nothing here')).toEqual([]);
    });
});

describe('filterSwitcherPodGroups', () => {
    const groups = [
        { organization: { id: 'personal' }, pods: [pod('1', 'inbox_crm'), pod('2', 'Odyssey')] },
        { organization: { id: 'gappy' }, pods: [pod('3', 'Ledflex Support')] },
    ];

    it('drops a group the query empties, heading and all', () => {
        const filtered = filterSwitcherPodGroups(groups, 'ledflex');
        expect(filtered).toHaveLength(1);
        expect(filtered[0].organization.id).toBe('gappy');
        expect(filtered[0].pods.map((match) => match.id)).toEqual(['3']);
    });

    it('keeps the rest of a group on the narrowed copy', () => {
        const filtered = filterSwitcherPodGroups(groups, 'odyssey');
        expect(filtered[0].organization).toEqual({ id: 'personal' });
        expect(filtered[0].pods.map((match) => match.id)).toEqual(['2']);
    });

    it('leaves every group standing when nothing was typed', () => {
        expect(filterSwitcherPodGroups(groups, '')).toEqual(groups);
    });
});
