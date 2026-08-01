import { describe, expect, it } from 'vitest';

import {
    buildSkillScaffold,
    isSkillFolderPath,
    isSkillManifestPath,
    isSkillsPath,
    isSkillsRootPath,
    readSkillManifest,
    skillManifestPath,
    skillNameFromPath,
    suggestSkillName,
    validateSkillName,
} from './skills';

describe('skill paths', () => {
    it('tells the shelf apart from a skill on it', () => {
        expect(isSkillsRootPath('/skills')).toBe(true);
        expect(isSkillsRootPath('/skills/')).toBe(true);
        expect(isSkillsRootPath('/skills/lemma-widget')).toBe(false);
        expect(isSkillFolderPath('/skills/lemma-widget')).toBe(true);
        expect(isSkillFolderPath('/skills/lemma-widget/scripts')).toBe(false);
    });

    it('does not claim a folder that merely starts with the same letters', () => {
        expect(isSkillsPath('/skills-archive')).toBe(false);
        expect(isSkillsPath('/me/skills')).toBe(false);
        expect(isSkillsPath('/skills/lemma-widget/scripts/build.sh')).toBe(true);
    });

    it('reads the owning skill out of a nested resource path', () => {
        expect(skillNameFromPath('/skills/lemma-widget/scripts/build.sh')).toBe('lemma-widget');
        expect(skillNameFromPath('/skills')).toBeNull();
        expect(skillNameFromPath('/docs/readme.md')).toBeNull();
    });

    it('recognizes only the manifest, not every markdown file in the folder', () => {
        expect(isSkillManifestPath('/skills/lemma-widget/SKILL.md')).toBe(true);
        expect(isSkillManifestPath('/skills/lemma-widget/NOTES.md')).toBe(false);
        expect(isSkillManifestPath('/skills/lemma-widget/nested/SKILL.md')).toBe(false);
        expect(skillManifestPath('lemma-widget')).toBe('/skills/lemma-widget/SKILL.md');
    });
});

describe('validateSkillName', () => {
    it('accepts what the loader accepts', () => {
        expect(validateSkillName('lemma-widget')).toBeNull();
        expect(validateSkillName('a')).toBeNull();
        expect(validateSkillName('report2')).toBeNull();
    });

    it('rejects what the loader would refuse', () => {
        expect(validateSkillName('')).not.toBeNull();
        expect(validateSkillName('Lemma-Widget')).not.toBeNull();
        expect(validateSkillName('-leading')).not.toBeNull();
        expect(validateSkillName('trailing-')).not.toBeNull();
        expect(validateSkillName('double--hyphen')).not.toBeNull();
        expect(validateSkillName('has space')).not.toBeNull();
        expect(validateSkillName('a'.repeat(65))).not.toBeNull();
    });
});

describe('suggestSkillName', () => {
    it('turns a typed title into a name the loader accepts', () => {
        expect(validateSkillName(suggestSkillName('Weekly Report'))).toBeNull();
        expect(suggestSkillName('Weekly Report')).toBe('weekly-report');
        expect(suggestSkillName('  Q4 — revenue!  ')).toBe('q4-revenue');
        expect(suggestSkillName('a'.repeat(80)).length).toBeLessThanOrEqual(64);
    });
});

describe('readSkillManifest', () => {
    const valid = '---\nname: weekly-report\ndescription: "Summarize the week"\n---\n\n# Weekly report\n';

    it('reports no problem for a skill the runtime will load', () => {
        expect(readSkillManifest(valid, 'weekly-report')).toEqual({
            name: 'weekly-report',
            description: 'Summarize the week',
            problem: null,
        });
    });

    it('catches the mismatch the loader raises on', () => {
        const manifest = readSkillManifest(valid, 'weekly-summary');

        expect(manifest.problem).toContain('must match');
    });

    it('names the missing piece rather than failing silently', () => {
        expect(readSkillManifest('# Just a doc\n', 'weekly-report').problem).toContain('No frontmatter');
        expect(readSkillManifest('---\nname: weekly-report\n---\n\nBody\n', 'weekly-report').problem)
            .toContain('description');
    });

    it('falls back to the folder name so the row still has a label', () => {
        expect(readSkillManifest('# Just a doc\n', 'weekly-report').name).toBe('weekly-report');
    });
});

describe('buildSkillScaffold', () => {
    it('produces a skill that loads the moment it is created', () => {
        const content = buildSkillScaffold('weekly-report', 'Summarize the week: every Monday');

        expect(readSkillManifest(content, 'weekly-report').problem).toBeNull();
    });
});
