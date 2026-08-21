import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const baselinePath = 'scripts/design-audit-baseline.json';
const baseline = JSON.parse(readFileSync(baselinePath, 'utf8'));
// A stable, zero-drift file used to exercise focused-path scanning. The
// previous fixture (pod-channels-panel.tsx) was deleted in ce6f75a9 without
// updating this test, which made every run fail on a target-read error.
const focusedPath = 'components/pod/resource-layout.tsx';

function runAudit(args) {
  return spawnSync('node', ['scripts/audit-design-system.mjs', ...args], {
    cwd: process.cwd(),
    encoding: 'utf8',
  });
}

function parseJson(result) {
  try {
    return JSON.parse(result.stdout);
  } catch (error) {
    throw new Error(`Expected JSON stdout. ${error.message}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  }
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function assertStatus(result, expected, label) {
  assert(
    result.status === expected,
    `${label} exited ${result.status}, expected ${expected}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
  );
}

const printedBaseline = runAudit(['--print-baseline']);
assertStatus(printedBaseline, 0, 'print baseline');
assert(
  JSON.stringify(parseJson(printedBaseline)) === JSON.stringify(baseline),
  'Printed baseline must match scripts/design-audit-baseline.json.',
);

const help = runAudit(['--help']);
assertStatus(help, 0, 'help');
assert(help.stdout.includes('Design system audit'), 'Help output should name the audit command.');
assert(help.stdout.includes('--baseline <path>'), 'Help output should document baseline usage.');
assert(help.stdout.includes('--changed'), 'Help output should document changed-file scans.');
assert(help.stdout.includes('--paths <path[,path...]>'), 'Help output should document focused path scans.');
assert(help.stdout.includes('design:audit:changed'), 'Help output should mention the changed audit command.');
assert(help.stdout.includes('design:audit:changed-queue'), 'Help output should mention the changed queue command.');
assert(help.stdout.includes('design:audit:focus'), 'Help output should mention the focused audit command.');
assert(help.stdout.includes('design:audit:queue'), 'Help output should mention the queue audit command.');
assert(!help.stdout.includes('Design system drift report'), 'Help output should not run the audit report.');

const queue = runAudit(['--queue', '--paths', focusedPath]);
assertStatus(queue, 0, 'focused queue audit');
assert(queue.stdout.includes('Design audit migration queue'), 'Queue output should name the migration queue.');
assert(queue.stdout.includes('Scope: focused paths'), 'Focused queue output should report focused path scope.');
assert(queue.stdout.includes(`Focused paths: ${focusedPath}`), 'Focused queue output should include the focused path.');
assert(queue.stdout.includes('Next non-assistant product files:'), 'Queue output should include product file queue.');
assert(
  queue.stdout.includes('Protected assistant files, do not migrate without focused assistant QA:'),
  'Queue output should preserve assistant caution.',
);

const focused = runAudit(['--json', '--summary', '--paths', focusedPath]);
assertStatus(focused, 0, 'focused path audit');
const focusedReport = parseJson(focused);
assert(focusedReport.metadata.targetMode === 'paths', 'Focused audit should report path scan mode.');
assert(focusedReport.metadata.filesScanned === 1, 'Focused audit should scan exactly one file.');
assert(focusedReport.metadata.targetErrors.length === 0, 'Focused audit should not have target errors.');

const sharedNativeFieldAudit = runAudit(['--json', '--summary', '--paths', 'components/flows/flow-editor.tsx']);
assertStatus(sharedNativeFieldAudit, 0, 'shared native field audit');
const flowEditorReport = parseJson(sharedNativeFieldAudit);
assert(
  flowEditorReport.informational.totals.rawFieldElements === 0,
  'Native fields using form-field-control variants should not count as raw field candidates.',
);
assert(
  flowEditorReport.informational.totals.rawButtonElements === 0,
  'Flow editor segmented controls, canvas nodes, rows, and add controls should use shared primitive markers instead of raw button candidates.',
);

const changed = runAudit(['--json', '--summary', '--changed']);
assertStatus(changed, 0, 'changed-file audit');
const changedReport = parseJson(changed);
assert(changedReport.metadata.targetMode === 'changed', 'Changed audit should report changed-file scan mode.');
assert(Array.isArray(changedReport.metadata.targetPaths), 'Changed audit should report the resolved changed paths.');
assert(changedReport.metadata.targetErrors.length === 0, 'Changed audit should not have target errors.');

const changedWithPaths = runAudit(['--json', '--summary', '--changed', '--paths', focusedPath]);
assertStatus(changedWithPaths, 1, 'changed with focused path');
assert(
  parseJson(changedWithPaths).metadata.targetErrors.some((error) => error.includes('Use either --changed or --paths')),
  'Changed audit should reject simultaneous --changed and --paths modes.',
);

const missingPath = runAudit(['--json', '--summary', '--paths', 'components/not-real.tsx']);
assertStatus(missingPath, 1, 'missing focused path');
assert(
  parseJson(missingPath).metadata.targetErrors.some((error) =>
    error.includes('Unable to read audit path "components/not-real.tsx"')
  ),
  'Missing focused path should report a target selection error.',
);

// An opening JSX tag cannot be matched by a regular expression, because its
// brace expressions nest arbitrarily. The patterns that tried stopped at the
// first `>` they saw outside two hand-spelled levels of nesting -- which, for
// `<button onClick={() => ...}>`, is the arrow's own `>`. Every such finding
// was reported truncated, and `allowedMatch` never saw the className it was
// meant to check. A truncated match is recognisable: it ends at an arrow.
const fullReport = runAudit(['--json', '--details']);
assertStatus(fullReport, 0, 'full details audit');
const reported = [];
JSON.stringify(parseJson(fullReport), (key, value) => {
  if (key === 'value' && typeof value === 'string') reported.push(value);
  return value;
});
assert(reported.length > 0, 'The details report should carry match values.');
const truncated = reported.filter((value) => /=>\s*$/.test(value));
assert(
  truncated.length === 0,
  `Tag matches must run to the tag's own '>', not an arrow's. Truncated: ${JSON.stringify(truncated.slice(0, 2))}`,
);

console.log('Design audit self-test passed.');
