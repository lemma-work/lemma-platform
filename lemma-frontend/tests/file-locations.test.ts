import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inLocation, parentFolder } from '../src/library/file-locations.ts';

test('personal files and skills never appear in shared files, including their root folders', () => {
    for (const path of ['/me', '/me/notes.md', '/skills', '/skills/review/SKILL.md']) assert.equal(inLocation(path, 'shared'), false);
    assert.equal(inLocation('/meeting-notes.md', 'shared'), true);
    assert.equal(inLocation('/skills-report.pdf', 'shared'), true);
    assert.equal(inLocation('/me/notes.md', 'personal'), true);
    assert.equal(inLocation('/members/list.md', 'personal'), false);
});
test('parent navigation stays inside the selected location', () => {
    assert.equal(parentFolder('/me', 'personal'), '/me');
    assert.equal(parentFolder('/me/projects', 'personal'), '/me');
    assert.equal(parentFolder('/skills/review/resources', 'skills'), '/skills/review');
    assert.equal(parentFolder('/skills', 'skills'), '/skills');
    assert.equal(parentFolder('/reports', 'shared'), '/');
});
