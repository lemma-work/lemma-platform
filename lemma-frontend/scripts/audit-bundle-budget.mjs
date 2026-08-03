import { brotliCompressSync } from 'node:zlib';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { isAbsolute, join, relative } from 'node:path';

/**
 * Bundle budget audit.
 *
 * The design audit keeps visual drift from accumulating one PR at a time. This
 * is the same ratchet pointed at payload: it reads a finished `next build` and
 * asserts that the bytes a browser must fetch before it can paint have not
 * grown past a recorded baseline.
 *
 * Everything here is measured from build output, never estimated from source.
 * Font preloads in particular are counted from the `<link rel="preload">` tags
 * in prerendered HTML rather than from Next's internal `-s.p.` filename
 * convention, so the number is exactly what the browser is told to fetch.
 */

const root = process.cwd();
const help = process.argv.includes('--help') || process.argv.includes('-h');
const strict = process.argv.includes('--strict');
const details = process.argv.includes('--details');
const json = process.argv.includes('--json');
const quiet = process.argv.includes('--quiet');
const printBaseline = process.argv.includes('--print-baseline');
const baselinePath = parseArgValue('--baseline');

const buildDir = join(root, '.next');

/**
 * Budgets are byte counts. `tolerance` is the slack a metric gets before it
 * fails: minifier output moves by a few hundred bytes between builds for
 * reasons no reviewer can act on, and a gate that cries wolf gets disabled.
 */
const METRICS = [
  {
    id: 'sharedJsBrotli',
    label: 'Shared JS, brotli (every route)',
    tolerance: 2048,
    describe:
      'Chunks in build-manifest rootMainFiles. Every route pays this before it renders.',
  },
  {
    id: 'fontPreloadBytes',
    label: 'Preloaded fonts (worst route)',
    tolerance: 1024,
    describe:
      'Font files a document tells the browser to fetch eagerly, worst prerendered route.',
  },
  {
    id: 'sharedJsRaw',
    label: 'Shared JS, uncompressed',
    tolerance: 8192,
    describe: 'Parse/compile cost — matters on low-end devices even when transfer is cheap.',
  },
  {
    id: 'totalClientJs',
    label: 'Total client JS, uncompressed',
    tolerance: 65536,
    describe: 'All emitted chunks. Grows when a route pulls in a new dependency.',
  },
];

if (help) {
  console.log(`
Usage: node scripts/audit-bundle-budget.mjs [options]

Reads a completed .next build and reports the bytes on the critical path.
Run \`npm run build\` first — this script never triggers a build itself.

  --baseline <path>   Compare against recorded budgets and report the delta.
  --strict            Exit non-zero when a metric exceeds its budget.
  --print-baseline    Emit current measurements as a baseline JSON file.
  --details           List the chunks and fonts behind each number.
  --json              Emit a parseable report.
  --quiet             On success print one line. Failures always print in full.
  --help, -h          Show this message.
`.trim());
  process.exit(0);
}

if (!existsSync(buildDir)) {
  fail(
    'No .next directory found.',
    'Run `npm run build` before auditing the bundle — this script measures build output, it does not produce it.',
  );
}

const manifestPath = join(buildDir, 'build-manifest.json');
if (!existsSync(manifestPath)) {
  fail(
    'No .next/build-manifest.json found.',
    'The .next directory exists but looks incomplete. Re-run `npm run build`.',
  );
}

// ---------------------------------------------------------------- measurement

const manifest = readJson(manifestPath);
const sharedFiles = (manifest.rootMainFiles || []).filter((file) => file.endsWith('.js'));

if (sharedFiles.length === 0) {
  fail(
    'build-manifest.json lists no rootMainFiles.',
    'Next may have changed its manifest shape. Update this script before trusting the gate.',
  );
}

const sharedChunks = sharedFiles.map((file) => {
  const buffer = readFileSync(join(buildDir, file));
  return { file, raw: buffer.length, brotli: brotliCompressSync(buffer).length };
});

const sharedJsRaw = sum(sharedChunks.map((chunk) => chunk.raw));
const sharedJsBrotli = sum(sharedChunks.map((chunk) => chunk.brotli));

const allChunks = collectFiles(join(buildDir, 'static'), '.js').map((file) => ({
  file: relative(buildDir, file),
  raw: statSync(file).size,
}));
const totalClientJs = sum(allChunks.map((chunk) => chunk.raw));

const { routes, documentsScanned } = measureFontPreloads();

/**
 * A partial build emits no HTML, and an unguarded measurement would then report
 * zero preloaded bytes and pass the gate — the failure mode a budget check
 * exists to prevent. Zero preloads across documents that *do* exist is a real
 * (and good) result; zero documents is a broken measurement.
 */
if (documentsScanned === 0) {
  fail(
    'No prerendered HTML found under .next/server/app.',
    'The build did not finish. Re-run `npm run build` and check it exits cleanly before auditing.',
  );
}

const worstRoute = routes[0] || { route: '(no font preloads)', bytes: 0, fonts: [] };
const fontPreloadBytes = worstRoute.bytes;

const measured = { sharedJsBrotli, fontPreloadBytes, sharedJsRaw, totalClientJs };

/**
 * Reads every prerendered document and totals the font files it preloads.
 *
 * Dynamic (`ƒ`) routes have no HTML at build time, so they cannot be measured
 * here. They share the same root layout as the prerendered ones, which is
 * where font preloads are decided — so the worst static route is a faithful
 * stand-in. If font declarations ever move into per-route layouts, this needs
 * to grow a runtime check.
 */
function measureFontPreloads() {
  const appDir = join(buildDir, 'server', 'app');
  if (!existsSync(appDir)) return { routes: [], documentsScanned: 0 };

  const documents = collectFiles(appDir, '.html');
  const results = [];
  for (const file of documents) {
    const html = readFileSync(file, 'utf8');
    const fonts = [];
    const seen = new Set();

    for (const tag of html.match(/<link[^>]+>/g) || []) {
      if (!/rel="preload"/.test(tag) || !/as="font"/.test(tag)) continue;
      const href = (tag.match(/href="([^"]+)"/) || [])[1];
      if (!href || seen.has(href)) continue;
      seen.add(href);

      const local = join(buildDir, href.replace(/^\/_next\//, ''));
      fonts.push({ href, bytes: existsSync(local) ? statSync(local).size : 0 });
    }

    if (fonts.length === 0) continue;
    results.push({
      route: '/' + relative(appDir, file).replace(/\.html$/, '').replace(/^index$/, ''),
      bytes: sum(fonts.map((font) => font.bytes)),
      fonts,
    });
  }

  return {
    routes: results.sort((a, b) => b.bytes - a.bytes),
    documentsScanned: documents.length,
  };
}

// -------------------------------------------------------------------- reports

if (printBaseline) {
  console.log(JSON.stringify(measured, null, 2));
  process.exit(0);
}

const baseline = baselinePath ? readJson(resolvePath(baselinePath)) : null;

const rows = METRICS.map((metric) => {
  const value = measured[metric.id];
  const budget = baseline ? baseline[metric.id] : null;
  const overBy =
    budget === null || budget === undefined ? 0 : value - budget - metric.tolerance;
  return { ...metric, value, budget, overBy, failed: overBy > 0 };
});

const failures = rows.filter((row) => row.failed);

if (json) {
  console.log(
    JSON.stringify(
      {
        measured,
        baseline,
        routes: routes.slice(0, 10),
        failures: failures.map((row) => ({ id: row.id, value: row.value, budget: row.budget })),
        ok: failures.length === 0,
      },
      null,
      2,
    ),
  );
  process.exit(strict && failures.length > 0 ? 1 : 0);
}

if (quiet && failures.length === 0) {
  console.log(
    `Bundle budget OK — shared JS ${kb(sharedJsBrotli)} brotli, preloaded fonts ${kb(fontPreloadBytes)}.`,
  );
  process.exit(0);
}

console.log('\nBundle budget\n');
for (const row of rows) {
  const budgetText =
    row.budget === null || row.budget === undefined
      ? 'no budget'
      : `budget ${kb(row.budget)}${row.budget ? ` (${signed(row.value - row.budget)})` : ''}`;
  const mark = row.failed ? 'FAIL' : row.budget ? 'ok  ' : '--  ';
  console.log(`  ${mark}  ${row.label.padEnd(34)} ${kb(row.value).padStart(10)}   ${budgetText}`);
}

if (details) {
  console.log('\nShared chunks:');
  for (const chunk of [...sharedChunks].sort((a, b) => b.raw - a.raw)) {
    console.log(`  ${kb(chunk.raw).padStart(10)} raw  ${kb(chunk.brotli).padStart(9)} br  ${chunk.file}`);
  }

  console.log(`\nHeaviest font preloads — ${worstRoute.route}:`);
  for (const font of [...worstRoute.fonts].sort((a, b) => b.bytes - a.bytes)) {
    console.log(`  ${kb(font.bytes).padStart(10)}  ${font.href}`);
  }

  console.log('\nLargest chunks overall:');
  for (const chunk of [...allChunks].sort((a, b) => b.raw - a.raw).slice(0, 10)) {
    console.log(`  ${kb(chunk.raw).padStart(10)}  ${chunk.file}`);
  }
}

if (failures.length > 0) {
  console.log('\nOver budget:\n');
  for (const row of failures) {
    console.log(`  ${row.label}`);
    console.log(`    ${row.describe}`);
    console.log(
      `    ${kb(row.value)} against a budget of ${kb(row.budget)} (+${kb(row.tolerance)} tolerance) — over by ${kb(row.overBy)}.\n`,
    );
  }
  console.log(
    'If the growth is intended, re-record the baseline:\n  npm run bundle:budget:baseline > scripts/bundle-budget-baseline.json\n',
  );
  process.exit(strict ? 1 : 0);
}

if (baseline) console.log('\nEvery metric is within budget.\n');
else
  console.log(
    '\nNo baseline given. Record one with:\n  npm run bundle:budget:baseline > scripts/bundle-budget-baseline.json\n',
  );

// ------------------------------------------------------------------- helpers

function collectFiles(dir, extension) {
  if (!existsSync(dir)) return [];
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) found.push(...collectFiles(full, extension));
    else if (entry.name.endsWith(extension)) found.push(full);
  }
  return found;
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    fail(`Could not read ${relative(root, path)}.`, error.message);
  }
}

function resolvePath(value) {
  return isAbsolute(value) ? value : join(root, value);
}

function parseArgValue(flag) {
  for (let index = 0; index < process.argv.length; index += 1) {
    const arg = process.argv[index];
    if (arg === flag) return process.argv[index + 1] || '';
    if (arg.startsWith(`${flag}=`)) return arg.slice(flag.length + 1);
  }
  return '';
}

function sum(values) {
  return values.reduce((total, value) => total + value, 0);
}

function kb(bytes) {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function signed(delta) {
  return `${delta >= 0 ? '+' : '-'}${kb(Math.abs(delta))}`;
}

function fail(message, hint) {
  console.error(`\nbundle-budget: ${message}`);
  if (hint) console.error(`  ${hint}`);
  console.error('');
  process.exit(1);
}
