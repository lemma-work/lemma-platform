import { describe, expect, it } from 'vitest';

import {
    DOC_SECTIONS,
    docSection,
    docSectionForPath,
    isPersonalPath,
    isPersonalRootPath,
    isSectionRoot,
} from './doc-sections';

describe('docSectionForPath', () => {
    it('places each root in its own section', () => {
        expect(docSectionForPath(null)).toBe('POD');
        expect(docSectionForPath('/')).toBe('POD');
        expect(docSectionForPath('/skills')).toBe('SKILLS');
        expect(docSectionForPath('/me')).toBe('PERSONAL');
    });

    it('keeps a subfolder in the section it was opened from', () => {
        expect(docSectionForPath('/skills/weekly-report/scripts')).toBe('SKILLS');
        expect(docSectionForPath('/me/drafts/q4')).toBe('PERSONAL');
        expect(docSectionForPath('/research/2026')).toBe('POD');
    });

    it('does not capture a pod folder that merely shares a prefix', () => {
        expect(docSectionForPath('/skills-archive')).toBe('POD');
        expect(docSectionForPath('/meeting-notes')).toBe('POD');
        expect(docSectionForPath('/team/me')).toBe('POD');
    });
});

describe('isSectionRoot', () => {
    it('is true for exactly the roots the switcher already offers', () => {
        expect(isSectionRoot('/skills')).toBe(true);
        expect(isSectionRoot('/me')).toBe(true);
        expect(isSectionRoot('/me/')).toBe(true);
    });

    it('is false for the pod root and for anything inside a section', () => {
        expect(isSectionRoot('/')).toBe(false);
        expect(isSectionRoot(null)).toBe(false);
        expect(isSectionRoot('/skills/weekly-report')).toBe(false);
        expect(isSectionRoot('/me/drafts')).toBe(false);
    });
});

describe('isPersonalPath', () => {
    it('separates the personal root from the files under it', () => {
        expect(isPersonalPath('/me')).toBe(true);
        expect(isPersonalPath('/me/notes.md')).toBe(true);
        expect(isPersonalRootPath('/me')).toBe(true);
        expect(isPersonalRootPath('/me/notes.md')).toBe(false);
        expect(isPersonalPath('/meeting-notes')).toBe(false);
    });
});

describe('docSection', () => {
    it('answers for every id, and falls back rather than throwing', () => {
        DOC_SECTIONS.forEach((section) => {
            expect(docSection(section.id).id).toBe(section.id);
        });
    });
});
