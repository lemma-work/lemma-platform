import { spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { isAbsolute, join, relative } from 'node:path';

const root = process.cwd();
const help = process.argv.includes('--help') || process.argv.includes('-h');
const strict = process.argv.includes('--strict');
const details = process.argv.includes('--details');
const json = process.argv.includes('--json');
const quiet = process.argv.includes('--quiet');
const queue = process.argv.includes('--queue');
const summary = process.argv.includes('--summary');
const printBaseline = process.argv.includes('--print-baseline');
const baselinePath = parseArgValue('--baseline');
const targets = ['app', 'components'];
const changedMode = hasArg('--changed');
const explicitPathMode = hasArg('--paths');
const pathFilters = parseArgList('--paths');
const extensions = new Set(['.css', '.tsx', '.ts']);
const ignoredSegments = new Set(['node_modules', '.next', 'public', 'vendor']);
const protectedUiFiles = new Map([
  [
    'components/lemma/assistant/assistant-experience.tsx',
    'Protected assistant experience surface. Report drift, but do not block unrelated design-system cleanup.',
  ],
  [
    'components/lemma/assistant/assistant-chrome.tsx',
    'Protected assistant chrome surface. Report drift, but do not block unrelated design-system cleanup.',
  ],
  [
    'components/lemma/assistant/assistant-experience-sidebar.tsx',
    'Protected assistant experience surface (extracted from assistant-experience.tsx). Report drift, but do not block unrelated design-system cleanup.',
  ],
  [
    'components/lemma/assistant/assistant-experience-header.tsx',
    'Protected assistant experience surface (extracted from assistant-experience.tsx). Report drift, but do not block unrelated design-system cleanup.',
  ],
  [
    'components/lemma/assistant/assistant-experience-conversation.tsx',
    'Protected assistant experience surface (extracted from assistant-experience.tsx). Report drift, but do not block unrelated design-system cleanup.',
  ],
  [
    'components/lemma/assistant/assistant-experience-composer.tsx',
    'Protected assistant experience surface (extracted from assistant-experience.tsx). Report drift, but do not block unrelated design-system cleanup.',
  ],
  [
    'components/lemma/assistant/assistant-experience-helpers.tsx',
    'Protected assistant experience surface (extracted from assistant-experience.tsx). Report drift, but do not block unrelated design-system cleanup.',
  ],
]);

if (help) {
  printHelp();
  process.exit(0);
}

function printHelp() {
  console.log(`Design system audit

Usage:
  node scripts/audit-design-system.mjs [options]

Core options:
  --strict                         Fail when strict/advisory product drift is found.
  --details                        Include line-number samples in the human report.
  --json                           Emit a parseable JSON report.
  --queue                          Print a concise non-assistant migration queue.
  --summary                        Omit samples from JSON output.
  --quiet                          On success, print one compact line. Failures still print details.
  --baseline <path>                Load informational and protected-assistant ratchet thresholds.
  --changed                        Audit changed app/components files in the current git worktree.
  --paths <path[,path...]>         Audit only specific app/components files or directories.
  --max-informational <id=count>   Set informational thresholds, comma-separated.
  --max-protected-assistant <id=count>
                                   Set protected assistant thresholds, comma-separated.
  --print-baseline                 Print the current baseline JSON shape and exit.
  --help, -h                       Show this help.

Common commands:
  npm run design:audit:details
  npm run design:audit:queue
  npm run design:audit:summary
  npm run design:audit:baseline
  npm run design:audit:changed
  npm run design:audit:changed-queue
  npm run design:audit:focus -- components/pod/pod-channels-panel.tsx
  npm run design:audit:ci`);
}

const checks = [
  {
    id: 'rawHex',
    label: 'raw hex colors',
    pattern: /#[0-9a-fA-F]{3,8}\b/g,
    allowed(path) {
      return path.endsWith('.svg') || path.includes('/landing/') || path.endsWith('app/globals.css');
    },
  },
  {
    id: 'arbitraryColor',
    label: 'arbitrary color utilities',
    pattern: /\b(?:bg|text|border|border-[trblxy]|ring|shadow|from|to|via|divide|outline|decoration|accent|fill|stroke)-\[[^\]]+\]/g,
    allowed(path) {
      return path.includes('/landing/');
    },
    allowedMatch(match) {
      if (/^stroke-\[[0-9.]+\]$/.test(match)) return true;
      return match.includes('var(--') && !/var\(--(?:brand-accent|interactive-primary|cta-bg|bg-surface|border-default)\)/.test(match);
    },
  },
  {
    id: 'oneOffRadius',
    label: 'one-off radius utilities',
    pattern: /\brounded-\[(?:[^\]]+)\]/g,
    allowed() {
      return false;
    },
  },
  {
    id: 'oneOffShadow',
    label: 'one-off shadow utilities',
    pattern: /\bshadow-\[(?:[^\]]+)\]/g,
    allowed(path) {
      return path.includes('/brand/');
    },
    allowedMatch(match) {
      return match.includes('var(--shadow-') || match.includes('var(--card-shadow)');
    },
  },
  {
    id: 'legacyAccent',
    label: 'legacy direct accent tokens',
    pattern: /var\(--(?:brand-accent|interactive-primary|cta-bg|bg-surface|border-default)\)/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'hardcodedNeutralUtility',
    label: 'hard-coded white/black utilities',
    pattern: /\b(?:bg|text|border|border-[trblxy]|ring|from|to|via|divide|outline|decoration|accent|fill|stroke)-(?:white|black)(?:\/\d+)?\b/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'scaledPaletteUtility',
    label: 'scaled Tailwind palette utilities',
    pattern: /\b(?:bg|text|border|border-[trblxy]|ring|shadow|from|to|via|divide|outline|decoration|accent|fill|stroke)-(?:primary|secondary|stone|neutral|zinc|slate|gray|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}(?:\/[0-9]+)?\b/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'arbitraryTypography',
    label: 'arbitrary typography utilities',
    pattern: /\b(?:tracking-\[(?:0\.[0-9]+em|[0-9.]+em)\]|text-\[(?:[0-9.]+px|[0-9.]+rem|[0-9.]+em)\]|leading-\[(?:[0-9.]+)\])/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'legacyFieldRecipe',
    label: 'legacy shadcn field recipes',
    pattern: /\b(?:border-input|ring-offset-background|placeholder:text-muted-foreground|focus:ring-ring)\b/g,
    allowed() {
      return false;
    },
  },
  {
    id: 'directBrandTokenUsage',
    label: 'direct brand token usage in product surfaces',
    pattern: /var\(--brand-[^)]+\)/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'shadcnSemanticUtility',
    label: 'shadcn semantic utility aliases',
    pattern: /(^|[\s"'`])(?:bg|text|border|ring|fill|stroke)-(?:background|foreground|muted|muted-foreground|border|primary|primary-foreground|secondary|secondary-foreground|accent|accent-foreground|destructive|destructive-foreground)(?:\/[0-9]+)?\b|(^|[\s"'`])bg-ring\b/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'agentBuilderInkFill',
    label: 'agent builder raw ink fills',
    pattern: /\b(?:bg|border)-\[var\(--text-primary\)\]|\btext-\[var\(--text-inverse\)\]/g,
    allowed(path) {
      return !path.endsWith('app/pod/[id]/agents/new/page.tsx');
    },
  },
  {
    id: 'scheduleBuilderInkFill',
    label: 'schedule builder raw ink fills',
    pattern: /\b(?:bg|border)-\[var\(--text-primary\)\]|\btext-\[var\(--text-inverse\)\]/g,
    allowed(path) {
      return !path.endsWith('app/pod/[id]/schedules/page.tsx');
    },
  },
  {
    id: 'podHeaderStepInkFill',
    label: 'pod header step raw ink fills',
    pattern: /\b(?:bg|border)-\[var\(--text-primary\)\]|\btext-\[var\(--text-inverse\)\]/g,
    allowed(path) {
      return !path.endsWith('components/pod/pod-page-header.tsx');
    },
  },
  {
    id: 'podHeaderLocalNavRecipes',
    label: 'pod header local nav recipes',
    pattern: /\binline-flex h-7 items-center gap-1\.5 rounded-md border border-transparent px-2\.5\b|\bitem\.active && 'bg-\[var\(--bg-subtle\)\] text-\[var\(--text-primary\)\]'|\btext-\[var\(--text-secondary\)\] hover:bg-\[var\(--bg-subtle\)\] hover:text-\[var\(--text-primary\)\]/g,
    allowed(path) {
      return !path.endsWith('components/pod/pod-page-header.tsx');
    },
  },
  {
    id: 'podHeaderLocalStepperRecipes',
    label: 'pod header local stepper recipes',
    pattern: /\bh-px w-7 bg-\[var\(--border-subtle\)\]|\binline-flex h-7 items-center gap-1\.5 rounded-md px-2 text-xs font-medium text-\[var\(--text-tertiary\)\]|\bborder-\[var\(--action-primary\)\] bg-\[var\(--action-primary\)\] text-\[var\(--text-on-brand\)\]|\bborder-\[var\(--text-secondary\)\]/g,
    allowed(path) {
      return !path.endsWith('components/pod/pod-page-header.tsx');
    },
  },
  {
    id: 'actionPrimaryInverseText',
    label: 'action-primary surfaces using text-inverse',
    pattern: /\bbg-\[var\(--action-primary\)\][^"'`]*\btext-\[var\(--text-inverse\)\]|\btext-\[var\(--text-inverse\)\][^"'`]*\bbg-\[var\(--action-primary\)\]/g,
    allowed() {
      return false;
    },
  },
  {
    id: 'functionBuilderRawHeaderTabs',
    label: 'function builder raw header tab indicators',
    pattern: /\bdata-\[state=active\]:border-\[var\(--text-primary\)\]/g,
    allowed(path) {
      return !path.endsWith('app/pod/[id]/functions/new/page.tsx');
    },
  },
  {
    id: 'workflowProgressInkLine',
    label: 'workflow progress lines using ink',
    pattern: /\bbg-\[var\(--text-primary\)\]/g,
    allowed(path) {
      return !path.endsWith('components/flows/flow-execution-panel.tsx');
    },
  },
  {
    id: 'rawInverseCodeSurfaces',
    label: 'raw inverse code surfaces',
    pattern: /\bbg-\[var\(--text-primary\)\]|\bborder-\[var\(--text-primary\)\]|\btext-\[var\(--text-inverse\)\]/g,
    allowed(path) {
      return !(
        path.endsWith('components/docs/docs-shell.tsx') ||
        path.endsWith('app/pod/[id]/kits/[kitId]/page.tsx')
      );
    },
  },
  {
    id: 'documentGridInverseSelection',
    label: 'document grid inverse selection affordances',
    pattern: /\btext-\[var\(--text-inverse\)\]/g,
    allowed(path) {
      return !path.endsWith('components/documents/documents-list.tsx');
    },
  },
  {
    id: 'assistantLocalTextMixes',
    label: 'assistant local text color mixes',
    pattern: /color-mix\(in_srgb,var\(--text-(?:primary|secondary)\)|var\(--text-(?:primary|secondary)\)_[0-9]+%/g,
    allowed(path) {
      return !(
        path.endsWith('components/lemma/assistant/assistant-experience.tsx') ||
        path.endsWith('components/lemma/assistant/assistant-chrome.tsx')
      );
    },
  },
  {
    id: 'productTextPrimaryMixRecipes',
    label: 'product text-primary mix recipes',
    pattern: /color-mix\(in_srgb,_?var\(--text-primary\)|var\(--text-primary\)_[0-9]+%/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'profileLocalSettingsRecipes',
    label: 'profile local settings recipes',
    pattern: /\bstate-surface-success\b|\bborder-\[color:var\(--row-border\)\]|\bborder-\[color:var\(--border-subtle\)\]|\bbg-\[var\(--surface-[12]\)\]|\bshadow-\[var\(--shadow-xs\)\]/g,
    allowed(path) {
      return !path.endsWith('app/(dashboard)/profile/page.tsx');
    },
  },
  {
    id: 'orgMembersLocalSettingsRecipes',
    label: 'org members local settings recipes',
    pattern: /\bCard(?:Header|Title|Description|Content)?\b|\bborder-transparent\b|\bhover:border-\[color:var\(--row-border\)\]|\bhover:bg-\[var\(--row-bg\)\]|\bborder-b border-\[color:var\(--border-subtle\)\]|\bbg-\[var\(--bg-subtle\)\]/g,
    allowed(path) {
      return !path.endsWith('app/organizations/[id]/settings/members/page.tsx');
    },
  },
  {
    id: 'orgSettingsNavLocalRecipes',
    label: 'org settings nav local recipes',
    pattern: /\brounded-full border px-3 py-1\.5\b|\bborder-\[color:var\(--row-border\)\]|\bborder-\[color:var\(--border-subtle\)\]|\bbg-\[var\(--bg-subtle\)\]|\bbg-\[var\(--surface-1\)\]|\bhover:border-\[color:var\(--row-border\)\]/g,
    allowed(path) {
      return !path.endsWith('components/organizations/organization-settings-nav.tsx');
    },
  },
  {
    id: 'podSettingsNavLocalRecipes',
    label: 'pod settings nav local recipes',
    pattern: /\brounded-full border px-3 py-1\.5\b|\bborder-\[color:var\(--row-border\)\]|\bborder-\[color:var\(--border-subtle\)\]|\bbg-\[var\(--bg-subtle\)\]|\bbg-\[var\(--surface-1\)\]|\bhover:border-\[color:var\(--row-border\)\]/g,
    allowed(path) {
      return !path.endsWith('components/pod/pod-settings-nav.tsx');
    },
  },
  {
    id: 'podMembersLocalSettingsRecipes',
    label: 'pod members local settings recipes',
    pattern: /\brounded-lg border border-\[color:var\(--border-subtle\)\] bg-\[var\(--bg-subtle\)\]|\brounded-lg border border-\[color:color-mix\(in_srgb,var\(--border-subtle\)_72%,transparent\)\] bg-\[var\(--bg-subtle\)\]|\bsurface-panel (?:flex|space-y-2)\b/g,
    allowed(path) {
      return !path.endsWith('app/pod/[id]/settings/members/page.tsx');
    },
  },
  {
    id: 'workspaceSidebarLocalRows',
    label: 'workspace sidebar local row recipes',
    pattern: /\bcustom-focus-ring flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-xs|\blemma-product-nav-item custom-focus-ring group flex h-7|\brounded-md bg-\[var\(--surface-2\)\] px-2 py-3 text-xs|\bbg-\[color:color-mix\(in_srgb,var\(--surface-2\)_72%,transparent\)\] text-\[var\(--text-primary\)\]|\bcustom-focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md text-\[var\(--text-tertiary\)\] hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bcustom-focus-ring flex h-7 items-center justify-center gap-1\.5 rounded-md text-xs text-\[var\(--text-secondary\)\] hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bflex cursor-pointer items-center (?:gap-2 px-3|justify-between gap-3 rounded-lg px-2) py-2 text-sm text-\[var\(--text-secondary\)\] outline-none transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]/g,
    allowed(path) {
      return !path.endsWith('components/pod/workspace-sidebar.tsx');
    },
  },
  {
    id: 'homeSidebarLocalRows',
    label: 'home sidebar local row recipes',
    pattern: /\bflex items-center gap-3 rounded-lg border border-transparent px-2 py-2|\bflex min-w-0 items-center gap-[23] rounded-lg border border-transparent px-2 py-2|\binline-flex items-center gap-2 rounded-md border border-transparent px-2 py-2|\bflex h-7 w-7 items-center justify-center rounded-md border(?: border-transparent)? transition-colors|\bborder-transparent hover:border-\[var\(--border-subtle\)\] hover:bg-\[var\(--surface-[12]\)\]/g,
    allowed(path) {
      return !path.endsWith('components/home/home-sidebar-chrome.tsx');
    },
  },
  {
    id: 'sharedShellLocalChrome',
    label: 'shared shell local chrome recipes',
    pattern: /\bhidden items-center gap-1 rounded-md px-1\.5 py-1 font-medium text-\[var\(--text-secondary\)\] transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\] sm:inline-flex|\bhidden rounded-md px-2 py-1 text-sm font-medium text-\[var\(--text-secondary\)\] transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\] md:inline-flex|\bcustom-focus-ring inline-flex h-[78] w-[78] items-center justify-center rounded-md text-\[var\(--text-(?:secondary|tertiary)\)\] transition-(?:colors|gentle) hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]/g,
    allowed(path) {
      return !(
        path.endsWith('app/pod/[id]/layout.tsx') ||
        path.endsWith('components/pod/workspace-sidebar.tsx')
      );
    },
  },
  {
    id: 'homeCardControlLocalChrome',
    label: 'home card control local chrome recipes',
    pattern: /\brounded-md border border-\[var\(--border-subtle\)\] bg-\[var\(--card-bg\)\][^"'`]*\b(?:shadow-\[var\(--shadow-xs\)\]|hover:border-\[var\(--row-border\)\])|\bflex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-\[var\(--border-subtle\)\] bg-\[var\(--delight-soft\)\]/g,
    allowed(path) {
      return !(
        path.endsWith('components/home/home-topbar.tsx') ||
        path.endsWith('components/home/home-workspace-overview.tsx')
      );
    },
  },
  {
    id: 'flowActionLocalChrome',
    label: 'flow action local chrome recipes',
    pattern: /\bcustom-focus-ring inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1\.5 text-\[var\(--text-tertiary\)\] transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bflex cursor-pointer items-center justify-between gap-3 rounded-lg px-2 py-2 text-sm text-\[var\(--text-secondary\)\] outline-none transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bcustom-focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-\[var\(--row-border\)\] bg-\[var\(--card-bg\)\] text-\[var\(--text-secondary\)\] transition-colors hover:bg-\[var\(--row-bg\)\] hover:text-\[var\(--text-primary\)\]|\binline-flex h-9 shrink-0 items-center gap-2 rounded-md px-2 text-sm font-medium text-\[var\(--text-secondary\)\] transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]/g,
    allowed(path) {
      return !(
        path.endsWith('app/pod/[id]/flows/[flowId]/page.tsx') ||
        path.endsWith('components/flows/flow-execution-panel.tsx')
      );
    },
  },
  {
    id: 'podHomeSchedulesActionChrome',
    label: 'pod home and schedules local action chrome recipes',
    pattern: /\binline-flex h-4 w-4 items-center justify-center rounded hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bcustom-focus-ring inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-\[var\(--text-secondary\)\] transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bcustom-focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-\[var\(--text-tertiary\)\] transition-colors hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bborder-\[var\(--border-subtle\)\] text-\[var\(--text-secondary\)\] hover:bg-\[var\(--surface-2\)\](?: hover:text-\[var\(--text-primary\)\])?/g,
    allowed(path) {
      return !(
        path.endsWith('app/pod/[id]/page.tsx') ||
        path.endsWith('app/pod/[id]/schedules/page.tsx')
      );
    },
  },
  {
    id: 'docsHomeSidebarFinalHoverChrome',
    label: 'docs and home sidebar final local hover chrome recipes',
    pattern: /\bgrid h-5 w-5 place-items-center rounded text-\[var\(--text-tertiary\)\] hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]|\bh-7 w-7 rounded-md border border-transparent bg-transparent text-\[var\(--text-secondary\)\] hover:border-\[var\(--border-subtle\)\] hover:bg-\[var\(--surface-2\)\] hover:text-\[var\(--text-primary\)\]/g,
    allowed(path) {
      return !(
        path.endsWith('components/docs/docs-nav.tsx') ||
        path.endsWith('components/home/home-sidebar-chrome.tsx')
      );
    },
  },
  {
    id: 'docsShellLocalSurfaceRecipes',
    label: 'docs shell local surface recipes',
    pattern: /\brounded-lg border border-\[var\(--card-border\)\] bg-\[var\(--card-bg\)\] p-5 shadow-\[var\(--card-shadow\)\]|\brounded-lg border border-\[var\(--row-border\)\] bg-\[var\(--row-bg\)\] p-4|\bhover-border-delight rounded-lg border border-\[var\(--card-border-subtle\)\] bg-\[var\(--card-bg\)\] p-4(?: text-right)? hover:bg-\[var\(--card-bg-hover\)\]|\bhover-border-delight group rounded-lg border border-\[var\(--card-border\)\] bg-\[var\(--card-bg\)\] p-5 shadow-\[var\(--card-shadow\)\] hover:bg-\[var\(--card-bg-hover\)\]|\boverflow-hidden rounded-lg border border-\[var\(--card-border\)\] bg-\[var\(--card-bg\)\]/g,
    allowed(path) {
      return !path.endsWith('components/docs/docs-shell.tsx');
    },
  },
  {
    id: 'dashboardLocalSurfaceRecipes',
    label: 'dashboard local surface recipes',
    pattern: /\brounded-lg border border-\[var\(--row-border\)\] bg-\[var\(--surface-1\)\] p-[16]|\brounded-md bg-\[var\(--surface-2\)\] p-3|\bgroup relative flex w-full items-start gap-4 rounded-lg border border-\[color:var\(--border-subtle\)\] bg-\[var\(--surface-1\)\] p-6 text-left transition-all hover:-translate-y-0\.5 hover:border-\[color:var\(--row-border\)\]|\bgroup relative overflow-hidden rounded-lg border border-\[color:var\(--border-subtle\)\] bg-\[var\(--surface-1\)\] p-6 transition-all duration-300 hover:-translate-y-0\.5 hover:border-\[color:var\(--row-border\)\]|\bflex h-40 items-center justify-center rounded-lg border border-\[var\(--row-border\)\] bg-\[var\(--surface-1\)\]|\bflex h-full flex-col overflow-hidden rounded-lg border border-\[var\(--row-border\)\] bg-\[var\(--surface-1\)\]/g,
    allowed(path) {
      return !(
        path.endsWith('components/dashboard/pod-card.tsx') ||
        path.endsWith('components/dashboard/compact-pod-list.tsx')
      );
    },
  },
  {
    id: 'documentsListLocalSurfaceRecipes',
    label: 'documents list local surface recipes',
    pattern: /\bhover-border-intelligence block rounded-lg border border-\[var\(--card-border\)\] bg-\[var\(--card-bg\)\] p-4 transition-all hover:shadow-\[var\(--shadow-sm\)\] group|\bbg-\[var\(--bg-canvas\)\] rounded-lg p-3 text-sm text-\[var\(--text-secondary\)\] leading-relaxed|\bgroup relative bg-\[var\(--card-bg\)\] border border-\[var\(--card-border\)\] rounded-lg p-4 transition-all hover:shadow-\[var\(--shadow-sm\)\] hover:border-\[var\(--border-strong\)\]|\bp-1\.5 rounded-lg bg-\[color:color-mix\(in_srgb,_var\(--card-bg\)_80%,_transparent\)\] backdrop-blur border border-\[var\(--card-border\)\] hover:bg-\[var\(--row-bg-hover\)\]|\bborder border-\[var\(--card-border\)\] rounded-lg overflow-hidden|\bgrid grid-cols-\[auto_1fr_120px_100px_40px\] gap-4 px-4 py-3 items-center border-b border-\[var\(--border-subtle\)\] last:border-0 hover:bg-\[var\(--bg-canvas\)\] transition-colors group|\bmin-w-\[140px\] bg-\[var\(--card-bg\)\] rounded-lg shadow-lg border border-\[var\(--card-border\)\] py-1 z-50|\bpx-3 py-2 text-sm cursor-pointer hover:bg-\[var\(--bg-canvas\)\] outline-none flex items-center gap-2|\bp-1 rounded hover:bg-\[var\(--bg-subtle\)\] opacity-0 group-hover:opacity-100 transition-opacity|\bgroup relative bg-\[var\(--card-bg\)\] border border-\[var\(--card-border\)\] rounded-lg overflow-hidden transition-all hover:shadow-\[var\(--shadow-sm\)\] hover:border-\[var\(--border-strong\)\]/g,
    allowed(path) {
      return !path.endsWith('components/documents/documents-list.tsx');
    },
  },
  {
    id: 'documentSpaceLocalSurfaceRecipes',
    label: 'document space local surface recipes',
    pattern: /\bh-10 rounded-lg bg-\[var\(--surface-1\)\] pl-9 text-sm shadow-none|\bcustom-focus-ring flex w-full items-start gap-3 border-b border-\[var\(--border-subtle\)\] px-4 py-3 text-left text-sm transition-colors last:border-b-0 hover:bg-\[var\(--surface-2\)\]|\bcustom-focus-ring flex h-14 w-full items-center gap-3 border-b border-\[var\(--border-subtle\)\] px-4 text-left text-sm transition-colors last:border-b-0 hover:bg-\[var\(--surface-2\)\]|\bmb-3 rounded-lg border border-\[var\(--border-subtle\)\] bg-\[var\(--surface-2\)\] px-3 py-2\.5|\brounded-lg border border-\[var\(--border-subtle\)\] bg-\[var\(--surface-1\)\] p-2/g,
    allowed(path) {
      return !path.endsWith('components/documents/document-space.tsx');
    },
  },
  {
    id: 'tableViewsLocalSurfaceRecipes',
    label: 'table views local surface recipes',
    pattern: /\bcursor-pointer rounded-lg border border-\[var\(--action-primary\)\] bg-\[var\(--action-primary-soft\)\] p-4 transition-gentle|\bcursor-pointer rounded-lg border border-\[color:var\(--border-subtle\)\] bg-\[var\(--surface-1\)\] p-4 transition-gentle hover:border-\[color:var\(--row-border\)\] hover:bg-\[var\(--bg-subtle\)\]|\bh-7 w-7 p-0 text-\[var\(--text-tertiary\)\] hover:bg-\[var\(--surface-1\)\] hover:text-\[var\(--text-primary\)\]|\bcursor-pointer rounded-lg border border-\[color:var\(--border-subtle\)\] bg-\[var\(--surface-1\)\] p-3 transition-gentle hover:border-\[color:var\(--row-border\)\] hover:shadow-\[var\(--shadow-xs\)\]/g,
    allowed(path) {
      return !(
        path.endsWith('components/tables/views/list-view.tsx')
      );
    },
  },
  {
    id: 'oversizedSurfacePanelRadius',
    label: 'oversized shared panel radius overrides',
    pattern: /\b(?:surface-panel|lemma-pop-card)[^"'`]*\brounded-(?:2xl|3xl)\b|\brounded-(?:2xl|3xl)\b[^"'`]*\b(?:surface-panel|lemma-pop-card)\b/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'staticSurfacePanelBlur',
    label: 'static surface-panel blur effects',
    pattern: /(?:^|[\s"'`])surface-panel(?=\s)[^"'`]*\bbackdrop-blur-(?:sm|md|lg|xl)\b|\bbackdrop-blur-(?:sm|md|lg|xl)\b[^"'`]*(?:^|[\s"'`])surface-panel(?=\s)/g,
    allowed(path) {
      return (
        path.endsWith('app/globals.css') ||
        path.includes('/landing/')
      );
    },
  },
  {
    id: 'lemmaPopCardSkinOverrides',
    label: 'lemma-pop-card skin overrides',
    pattern: /\blemma-pop-card[^"'`]*\b(?:bg|border|shadow)-\[[^\]]+\]|\b(?:bg|border|shadow)-\[[^\]]+\][^"'`]*\blemma-pop-card\b/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'redundantSharedPanelRadius',
    label: 'redundant shared panel radius utilities',
    pattern: /\b(?:surface-panel|surface-panel-muted|surface-panel-dashed|lemma-pop-card)[^"'`]*\brounded-lg\b|\brounded-lg\b[^"'`]*\b(?:surface-panel|surface-panel-muted|surface-panel-dashed|lemma-pop-card)\b/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'surfacePanelDashedBaseFillOverrides',
    label: 'surface-panel-dashed base fill overrides',
    pattern: /\bsurface-panel-dashed[^"'`]*\bbg-transparent\b|\bbg-transparent\b[^"'`]*\bsurface-panel-dashed\b/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'cardSurfacePanelDuplication',
    label: 'Card callers repeating surface-panel',
    pattern: /<Card\b[^>\n]*className=(?:\{[^}\n]*surface-panel[^}\n]*\}|"[^"\n]*surface-panel[^"\n]*")/g,
    allowed(path) {
      return path.endsWith('components/ui/card.tsx') || path.includes('/landing/');
    },
    allowedMatch(match) {
      return !/(?:^|[\s"'`])surface-panel(?:[\s"'`]|$)/.test(match);
    },
  },
  {
    id: 'cardRedundantRadius',
    label: 'Card callers repeating rounded-lg',
    pattern: /<Card\b[^>\n]*className=(?:\{[^}\n]*rounded-lg[^}\n]*\}|"[^"\n]*rounded-lg[^"\n]*")/g,
    allowed(path) {
      return path.endsWith('components/ui/card.tsx') || path.includes('/landing/');
    },
  },
  {
    id: 'buttonPrimarySkinDuplication',
    label: 'Button callers repeating primary skin',
    pattern: /<Button\b[^>\n]*className=(?:\{[^}\n]*(?:bg-\[var\(--action-primary\)\]|text-\[var\(--text-on-brand\)\]|hover:bg-\[var\(--action-primary-hover\)\])[^}\n]*\}|"[^"\n]*(?:bg-\[var\(--action-primary\)\]|text-\[var\(--text-on-brand\)\]|hover:bg-\[var\(--action-primary-hover\)\])[^"\n]*")/g,
    allowed(path) {
      return path.endsWith('components/ui/button.tsx') || path.includes('/landing/');
    },
  },
];

const advisoryChecks = [
  {
    id: 'localToneRecipe',
    label: 'advisory local tone recipes',
    pattern: /color-mix\(in[_ ]srgb,[_ ]*var\(--(?:state-success|state-warning|state-error|state-info|action-primary|attention|delight|intelligence|collaboration)\)[^)]+\)/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'bespokePanelRecipe',
    label: 'advisory bespoke panel recipes',
    pattern: /\brounded-(?:xl|2xl|3xl)\b(?=[^"'`]*\b(?:border|shadow-(?:sm|md|lg|xl)|shadow-\[var\(--shadow-))/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
  {
    id: 'deskLocalHoverRecipe',
    label: 'advisory local desk hover recipes',
    pattern: /color-mix\(in[_ ]srgb,[_ ]*color-mix\(in[_ ]srgb,[_ ]*var\(--text-primary\)_88%,[_ ]*transparent\)_5%,[_ ]*transparent\)/g,
    allowed(path) {
      return path.endsWith('app/globals.css') || path.includes('/landing/');
    },
  },
];

const informationalChecks = [
  {
    id: 'rawButtonElements',
    label: 'Raw button elements, review before migrating',
    pattern: /<button\b(?:(?:"[^"]*"|'[^']*'|\{(?:[^{}]|\{[^{}]*\})*\}|[^>])*)>/g,
    allowed(path) {
      return path.endsWith('components/ui/button.tsx') || path.includes('/landing/');
    },
    allowedMatch(match) {
      return (
        /\brole=["']switch["']/.test(match) ||
        /\bsegmented-control-item\b/.test(match) ||
        /\bchip\b/.test(match) ||
        /\bflow-canvas-node-button\b/.test(match) ||
        /\bflow-editor-row-button\b/.test(match) ||
        /\bflow-editor-add-button\b/.test(match) ||
        /\bagent-builder-step-button\b/.test(match) ||
        /\bagent-builder-choice-button\b/.test(match) ||
        /\bsurface-picker-button\b/.test(match) ||
        /\blemma-sidebar-row\b/.test(match) ||
        /\blemma-sidebar-icon-button\b/.test(match) ||
        /\blemma-shell-icon-button\b/.test(match) ||
        /\bworkspace-sidebar-trigger-button\b/.test(match) ||
        /\bworkspace-sidebar-inline-action-button\b/.test(match) ||
        /\bflow-execution-row-button\b/.test(match) ||
        /\bflow-execution-trace-button\b/.test(match) ||
        /\bflow-execution-rail-button\b/.test(match) ||
        /\bflow-execution-inline-button\b/.test(match) ||
        /\bresource-add-trigger\b/.test(match) ||
        /\bresource-remove-button\b/.test(match) ||
        /\bresource-mode-toggle-button\b/.test(match) ||
        /\bresource-warning-button\b/.test(match) ||
        /\blemma-quiet-icon-button\b/.test(match) ||
        /\blemma-card-icon-control\b/.test(match) ||
        /\bdocument-search-clear-button\b/.test(match) ||
        /\bdocument-search-mode-button\b/.test(match) ||
        /\bdocument-selection-button\b/.test(match) ||
        /\brecord-editor-boolean-button\b/.test(match) ||
        /\bexpression-builder-chip-button\b/.test(match) ||
        /\bexpression-builder-operator-button\b/.test(match) ||
        /\btable-builder-remove-button\b/.test(match) ||
        /\brecord-detail-boolean-button\b/.test(match) ||
        /\bhome-sidebar-surface-button\b/.test(match) ||
        /\bhome-sidebar-section-button\b/.test(match) ||
        /\bhome-sidebar-rail-button\b/.test(match) ||
        /\beditable-cell-boolean-button\b/.test(match) ||
        /\bagent-runtime-trigger-button\b/.test(match) ||
        /\bagent-runtime-harness-button\b/.test(match) ||
        /\bagent-runtime-model-button\b/.test(match) ||
        /\bagent-runtime-scope-button\b/.test(match) ||
        /\bmodel-picker-choice-button\b/.test(match) ||
        /\bmodels-settings-scope-button\b/.test(match) ||
        /\bmodels-settings-provider-button\b/.test(match) ||
        /\bworkspace-sidebar-suggestion-chip-button\b/.test(match) ||
        /\bdocument-space-result-button\b/.test(match) ||
        /\bdocument-space-entry-button\b/.test(match) ||
        /\bdocument-space-inline-button\b/.test(match) ||
        /\bfilter-builder-remove-button\b/.test(match) ||
        /\btheme-toggle-icon-button\b/.test(match) ||
        /\btheme-toggle-pill-button\b/.test(match) ||
        /\bfunction-test-run-button\b/.test(match) ||
        /\bschedule-operation-chip\b/.test(match) ||
        /\bschedule-choice-card-button\b/.test(match) ||
        /\bschedule-progress-step-button\b/.test(match) ||
        /\bhome-explainer-nav-button\b/.test(match) ||
        /\bhome-explainer-dot-button\b/.test(match) ||
        /\bdata-folder-trigger-button\b/.test(match) ||
        /\bdata-search-result-button\b/.test(match) ||
        /\bdata-file-list-entry-button\b/.test(match) ||
        /\bdata-file-grid-entry-button\b/.test(match) ||
        /\bflow-new-template-card-button\b/.test(match) ||
        /\bflow-new-progress-step-button\b/.test(match) ||
        /\bflows-index-card-action-button\b/.test(match) ||
        /\bflows-index-row-action-button\b/.test(match) ||
        /\bresource-title-button\b/.test(match) ||
        /\bresource-metric-button\b/.test(match) ||
        /\benum-option-remove-button\b/.test(match) ||
        /\bprofile-org-choice-button\b/.test(match) ||
        /\bagent-avatar-option-button\b/.test(match) ||
        /\bhome-topbar-org-button\b/.test(match) ||
        /\bhome-topbar-user-button\b/.test(match) ||
        /\bflow-detail-switcher-button\b/.test(match) ||
        /\bflow-detail-icon-upload-button\b/.test(match) ||
        /\bfunctions-index-menu-button\b/.test(match) ||
        /\bdatastore-table-expand-button\b/.test(match) ||
        /\bauth-portal-session-button\b/.test(match) ||
        /\brecord-grid-open-button\b/.test(match) ||
        /\bresource-feedback-action-button\b/.test(match) ||
        /\bagent-test-history-button\b/.test(match) ||
        /\bagent-test-summary-button\b/.test(match) ||
        /\bsetup-secondary-action-button\b/.test(match) ||
        /\bsetup-domain-toggle\b/.test(match) ||
        /\bsetup-example-button\b/.test(match) ||
        /\bsetup-defer-button\b/.test(match) ||
        /\bsetup-path-choice\b/.test(match) ||
        /\bsetup-kit-option\b/.test(match) ||
        /\bhome-hero-starter-button\b/.test(match) ||
        /\bhome-hero-file-remove-button\b/.test(match) ||
        /\bhome-hero-scope-button\b/.test(match) ||
        /\bchat-inline-file-button\b/.test(match) ||
        /\bchat-inline-close-button\b/.test(match) ||
        /\bchat-tool-toggle-button\b/.test(match) ||
        /\boffice-chip\b/.test(match) ||
        /\bpod-home-send-button\b/.test(match) ||
        /\bpod-assistant-file-button\b/.test(match) ||
        /\bpod-assistant-scrim-button\b/.test(match) ||
        /\bconversation-list-row-button\b/.test(match) ||
        /\blemma-assistant-empty-state-suggestion-button\b/.test(match) ||
        /\blemma-assistant-inline-tool-button\b/.test(match) ||
        /\blemma-assistant-inline-approval-button\b/.test(match) ||
        /\blemma-assistant-tool-rollup-toggle-button\b/.test(match) ||
        /\blemma-assistant-tool-group-button\b/.test(match) ||
        /\blemma-assistant-sidebar-conversation-button\b/.test(match) ||
        /\blemma-assistant-resource-mention-button\b/.test(match) ||
        /\blemma-assistant-conversation-row-button\b/.test(match) ||
        /\blemma-assistant-runtime-trigger-button\b/.test(match) ||
        /\blemma-assistant-runtime-choice-button\b/.test(match) ||
        /\blemma-assistant-runtime-group-button\b/.test(match) ||
        /\blemma-assistant-file-remove-button\b/.test(match)
      );
    },
  },
  {
    id: 'rawSwitchButtonElements',
    label: 'Raw switch button elements, review before migrating',
    pattern: /<button\b(?:(?:"[^"]*"|'[^']*'|\{(?:[^{}]|\{[^{}]*\})*\}|[^>])*)>/g,
    allowed(path) {
      return path.endsWith('components/ui/button.tsx') || path.endsWith('components/ui/switch.tsx') || path.includes('/landing/');
    },
    allowedMatch(match) {
      return !/\brole=["']switch["']/.test(match);
    },
  },
  {
    id: 'rawFieldElements',
    label: 'Raw input/textarea/select elements, review before migrating',
    pattern: /<(?:input|textarea|select)\b(?:(?:"[^"]*"|'[^']*'|\{[^}]*\}|[^>])*)>/g,
    allowed(path) {
      return (
        path.endsWith('components/ui/input.tsx') ||
        path.endsWith('components/ui/textarea.tsx') ||
        path.includes('/landing/')
      );
    },
    allowedMatch(match) {
      if (/^<input\b/.test(match) && /\btype=(?:["'](?:checkbox|file|hidden|radio)["']|\{["'](?:checkbox|file|hidden|radio)["']\})/.test(match)) {
        return true;
      }

      if (/\bform-field-control(?:-flat)?\b/.test(match)) {
        return true;
      }

      if (/\binline-edit-field\b/.test(match)) {
        return true;
      }

      if (/\bdocument-search-field\b/.test(match)) {
        return true;
      }

      if (/\brecord-editor-field\b/.test(match)) {
        return true;
      }

      if (/\bexpression-builder-field\b/.test(match)) {
        return true;
      }

      if (/\btable-builder-field\b/.test(match)) {
        return true;
      }

      if (/\brecord-detail-field\b/.test(match)) {
        return true;
      }

      if (/\beditable-cell-field\b/.test(match)) {
        return true;
      }

      if (/\bfilter-builder-field\b/.test(match)) {
        return true;
      }

      if (/\badd-column-field\b/.test(match)) {
        return true;
      }

      if (/\benum-option-field\b/.test(match)) {
        return true;
      }

      if (/\bdocument-viewer-source-field\b/.test(match)) {
        return true;
      }

      if (/\bagent-test-field\b/.test(match)) {
        return true;
      }

      return /<(?:input|textarea)\b/.test(match) && /\bbg-transparent\b/.test(match);
    },
  },
  {
    id: 'inlineEditableFieldElements',
    label: 'Inline editable input/textarea elements, review before migrating',
    pattern: /<(?:input|textarea)\b(?:(?:"[^"]*"|'[^']*'|\{[^}]*\}|[^>])*)>/g,
    allowed(path) {
      return (
        path.endsWith('components/ui/input.tsx') ||
        path.endsWith('components/ui/textarea.tsx') ||
        path.includes('/landing/')
      );
    },
    allowedMatch(match) {
      if (/^<input\b/.test(match) && /\btype=(?:["'](?:checkbox|file|hidden|radio)["']|\{["'](?:checkbox|file|hidden|radio)["']\})/.test(match)) {
        return true;
      }

      return /\binline-edit-field\b/.test(match) || !/\bbg-transparent\b/.test(match);
    },
  },
  {
    id: 'cardSkinOverrides',
    label: 'Card skin overrides, review before migrating',
    pattern: /<Card\b[^>\n]*className=(?:\{[^}\n]*(?:bg-\[|border-\[|shadow-none|shadow-\[)[^}\n]*\}|"[^"\n]*(?:bg-\[|border-\[|shadow-none|shadow-\[)[^"\n]*")/g,
    allowed(path) {
      return path.endsWith('components/ui/card.tsx') || path.includes('/landing/');
    },
  },
  {
    id: 'buttonSkinOverrides',
    label: 'Button skin overrides, review before migrating',
    pattern: /<Button\b[^>\n]*className=(?:\{[^}\n]*(?:bg-\[|border-\[|shadow-\[)[^}\n]*\}|"[^"\n]*(?:bg-\[|border-\[|shadow-\[)[^"\n]*")/g,
    allowed(path) {
      return path.endsWith('components/ui/button.tsx') || path.includes('/landing/');
    },
    allowedMatch(match) {
      return (
        /\bfunctions-index-peer-button\b/.test(match) ||
        /\bdatastore-table-toolbar-button\b/.test(match)
      );
    },
  },
  {
    id: 'badgePresentationOverrides',
    label: 'Badge presentation overrides, review before migrating',
    pattern: /<Badge\b[^>\n]*className=(?:\{[^}\n]*(?:rounded-|px-|py-|text-xs|uppercase|w-fit)[^}\n]*\}|"[^"\n]*(?:rounded-|px-|py-|text-xs|uppercase|w-fit)[^"\n]*")/g,
    allowed(path) {
      return path.endsWith('components/ui/badge.tsx') || path.includes('/landing/');
    },
    allowedMatch(match) {
      return (
        /\bflow-execution-badge(?:-\w+)?\b/.test(match) ||
        /\bfunction-test-(?:value|status)-badge\b/.test(match) ||
        /\bfunction-editor-status-badge\b/.test(match) ||
        /\blemma-assistant-plan-active-badge\b/.test(match) ||
        /\blemma-assistant-approval-status-badge\b/.test(match) ||
        /\blemma-assistant-widget-status-badge\b/.test(match) ||
        /\blemma-assistant-question-count-badge\b/.test(match) ||
        /\blemma-assistant-rank-badge\b/.test(match) ||
        /\blemma-assistant-presented-file-badge\b/.test(match)
      );
    },
  },
  {
    id: 'viewportHeightWithoutDvhFallback',
    label: 'CSS 100vh declarations without a 100dvh fallback on the next line',
    // 100vh overshoots the visible area on mobile browsers with collapsing
    // toolbars. Pair every (min-/max-)height: 100vh with a dvh fallback.
    pattern: /(?:min-|max-)?height:\s*100vh\s*;(?!\s*(?:min-|max-)?height:\s*100dvh)/g,
    allowed() {
      return false;
    },
  },
  {
    id: 'hoverOnlyDisplayReveal',
    label: 'Actions revealed via group-hover display/visibility, unreachable on touch',
    // The global touch override in styles/utilities.css only covers the
    // opacity-0/group-hover:opacity-100 idiom; display- and visibility-based
    // reveals stay broken on touch devices, so flag them at the source.
    pattern: /(?:\b(?:hidden|invisible)\b[^"'\n]*group-hover:(?:visible|flex|block|grid|inline-flex)\b|\bgroup-hover:(?:visible|flex|block|grid|inline-flex)\b[^"'\n]*\b(?:hidden|invisible)\b)/g,
    allowed() {
      return false;
    },
  },
  {
    id: 'fixedWidthsWiderThanPhones',
    label: 'Unconditional fixed widths of 360px+ that overflow phone viewports',
    // w-[360px]+ without a responsive prefix or a max-w-* clamp on the same
    // class list forces horizontal overflow at 375px. Use min()/max-w or a
    // breakpoint-prefixed width instead.
    pattern: /(?<![:\w-])(?<!\bhidden\b[^"'\n]*)w-\[(?:3[6-9]\d|[4-9]\d\d|\d{4})px\](?![^"'\n]*max-w-)/g,
    allowed(path) {
      return path.includes('/landing/');
    },
  },
];

const protectedAssistantChecks = [...checks, ...advisoryChecks, ...informationalChecks];
const thresholdBaseline = readThresholdBaseline(baselinePath);
const informationalThresholds = parseThresholdArgs('--max-informational', informationalChecks, {
  baselineThresholds: thresholdBaseline.informational,
  baselineErrors: thresholdBaseline.errors,
});
const protectedAssistantThresholds = parseThresholdArgs('--max-protected-assistant', protectedAssistantChecks, {
  baselineThresholds: thresholdBaseline.protectedAssistant,
  baselineErrors: thresholdBaseline.errors,
});

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    if (ignoredSegments.has(entry)) continue;

    const fullPath = join(dir, entry);
    const stat = statSync(fullPath);

    if (stat.isDirectory()) {
      walk(fullPath, files);
      continue;
    }

    const extension = fullPath.slice(fullPath.lastIndexOf('.'));
    if (extensions.has(extension)) files.push(fullPath);
  }

  return files;
}

function hasArg(flag) {
  return process.argv.some((arg) => arg === flag || arg.startsWith(`${flag}=`));
}

function parseArgList(flag) {
  const values = [];

  for (let index = 0; index < process.argv.length; index += 1) {
    const arg = process.argv[index];
    let value = null;

    if (arg === flag) {
      const next = process.argv[index + 1] || '';
      value = next.startsWith('--') ? '' : next;
    } else if (arg.startsWith(`${flag}=`)) {
      value = arg.slice(flag.length + 1);
    }

    if (!value) continue;

    for (const item of value.split(',')) {
      const trimmed = item.trim();
      if (trimmed) values.push(trimmed);
    }
  }

  return values;
}

function isInsideRoot(rel) {
  return rel && !rel.startsWith('..') && !rel.startsWith('/');
}

function isInsideAuditTargets(rel) {
  return targets.some((target) => rel === target || rel.startsWith(`${target}/`));
}

function hasIgnoredSegment(rel) {
  return rel.split('/').some((segment) => ignoredSegments.has(segment));
}

function isAuditableFile(fullPath) {
  const rel = relative(root, fullPath);
  const extension = fullPath.slice(fullPath.lastIndexOf('.'));

  return (
    isInsideRoot(rel) &&
    isInsideAuditTargets(rel) &&
    !hasIgnoredSegment(rel) &&
    extensions.has(extension)
  );
}

function collectFocusedFiles(paths) {
  const errors = [];
  const focusedFiles = [];

  if (changedMode && explicitPathMode) {
    errors.push('Use either --changed or --paths, not both.');
  }

  if (explicitPathMode && paths.length === 0) {
    errors.push('--paths requires a comma-separated file or directory path.');
  }

  for (const path of paths) {
    const fullPath = isAbsolute(path) ? path : join(root, path);
    const rel = relative(root, fullPath);

    if (!isInsideRoot(rel) || !isInsideAuditTargets(rel)) {
      errors.push(`Audit path "${path}" must be inside app/ or components/.`);
      continue;
    }

    if (hasIgnoredSegment(rel)) {
      errors.push(`Audit path "${path}" includes an ignored directory segment.`);
      continue;
    }

    try {
      const stat = statSync(fullPath);

      if (stat.isDirectory()) {
        focusedFiles.push(...walk(fullPath).filter(isAuditableFile));
      } else if (stat.isFile() && isAuditableFile(fullPath)) {
        focusedFiles.push(fullPath);
      } else {
        errors.push(`Audit path "${path}" is not a supported .css, .ts, or .tsx file.`);
      }
    } catch (error) {
      errors.push(`Unable to read audit path "${path}": ${error.message}`);
    }
  }

  return {
    files: [...new Set(focusedFiles)],
    errors,
  };
}

function gitPathList(args, errors) {
  const result = spawnSync('git', args, {
    cwd: root,
    encoding: 'utf8',
  });

  if (result.status !== 0) {
    errors.push(`Unable to read changed files with git ${args.join(' ')}: ${result.stderr.trim() || result.stdout.trim()}`);
    return [];
  }

  return result.stdout
    .split('\n')
    .map((path) => path.trim())
    .filter(Boolean);
}

function gitPrefix(errors) {
  const result = spawnSync('git', ['rev-parse', '--show-prefix'], {
    cwd: root,
    encoding: 'utf8',
  });

  if (result.status !== 0) {
    errors.push(`Unable to read git path prefix: ${result.stderr.trim() || result.stdout.trim()}`);
    return '';
  }

  return result.stdout.trim();
}

function normalizeGitPath(path, prefix) {
  if (!prefix) return path;
  return path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

function collectChangedFiles() {
  const errors = [];
  const prefix = gitPrefix(errors);
  const changedPaths = new Set([
    ...gitPathList(['diff', '--name-only', '--diff-filter=ACMR', '--', ...targets], errors).map((path) => normalizeGitPath(path, prefix)),
    ...gitPathList(['diff', '--cached', '--name-only', '--diff-filter=ACMR', '--', ...targets], errors).map((path) => normalizeGitPath(path, prefix)),
    ...gitPathList(['ls-files', '--others', '--exclude-standard', '--', ...targets], errors).map((path) => normalizeGitPath(path, prefix)),
  ]);
  const changedFiles = [];

  for (const path of changedPaths) {
    const fullPath = join(root, path);

    if (isAuditableFile(fullPath)) {
      changedFiles.push(fullPath);
    }
  }

  return {
    files: [...new Set(changedFiles)],
    paths: [...changedPaths].filter((path) => isAuditableFile(join(root, path))).sort(),
    errors,
  };
}

function emptyTotals(checkList) {
  return Object.fromEntries(checkList.map((check) => [check.id, 0]));
}

function rankFiles(byFile) {
  return [...byFile.entries()]
    .map(([file, counts]) => {
      const visibleCounts = Object.fromEntries(Object.entries(counts).filter(([key]) => key !== '__samples'));

      return {
        file,
        total: Object.values(visibleCounts).reduce((sum, count) => sum + count, 0),
        counts: visibleCounts,
        samples: counts.__samples || [],
      };
    })
    .sort((a, b) => b.total - a.total || a.file.localeCompare(b.file))
    .slice(0, 20);
}

function lineNumberAt(source, index) {
  let line = 1;
  for (let cursor = 0; cursor < index; cursor += 1) {
    if (source.charCodeAt(cursor) === 10) line += 1;
  }
  return line;
}

function findMatches(source, check, rel) {
  const matches = [];
  for (const match of source.matchAll(check.pattern)) {
    const value = match[0];
    if (check.allowedMatch?.(value, rel)) continue;
    matches.push({
      value,
      line: lineNumberAt(source, match.index ?? 0),
    });
  }
  return matches;
}

function collect(checkList) {
  const totals = Object.fromEntries(checkList.map((check) => [check.id, 0]));
  const protectedTotals = emptyTotals(checkList);
  const byFile = new Map();
  const protectedByFile = new Map();

  for (const file of files) {
    const rel = relative(root, file);
    const source = readFileSync(file, 'utf8');
    const isProtected = protectedUiFiles.has(rel);

    for (const check of checkList) {
      if (check.allowed(rel)) continue;

      const matches = findMatches(source, check, rel);
      if (!matches.length) continue;

      const activeTotals = isProtected ? protectedTotals : totals;
      const activeByFile = isProtected ? protectedByFile : byFile;

      activeTotals[check.id] += matches.length;
      const existing = activeByFile.get(rel) || {};
      existing[check.id] = matches.length;
      existing.__samples ||= [];
      for (const match of matches.slice(0, 3)) {
        if (existing.__samples.length >= 6) break;
        existing.__samples.push({ checkId: check.id, line: match.line, value: match.value });
      }
      activeByFile.set(rel, existing);
    }
  }

  return {
    totals,
    rankedFiles: rankFiles(byFile),
    protectedTotals,
    protectedRankedFiles: rankFiles(protectedByFile),
  };
}

function printRankedEntries(entries, checkList, options = {}) {
  const { includeSamples = false } = options;

  for (const entry of entries) {
    const detail = checkList
      .filter((check) => entry.counts[check.id])
      .map((check) => `${check.id}=${entry.counts[check.id]}`)
      .join(', ');
    const note = protectedUiFiles.get(entry.file);
    console.log(`- ${entry.file}: ${entry.total} (${detail})${note ? ` — ${note}` : ''}`);

    if (!includeSamples || !entry.samples.length) continue;

    for (const sample of entry.samples) {
      const normalized = sample.value.replace(/\s+/g, ' ').trim();
      const preview = normalized.length > 96 ? `${normalized.slice(0, 93)}...` : normalized;
      console.log(`  - L${sample.line} ${sample.checkId}: ${preview}`);
    }
  }

  if (entries.length === 0) {
    console.log('- none');
  }
}

function printQueueEntries(entries) {
  for (const entry of entries) {
    const detail = Object.entries(entry.counts)
      .filter(([key]) => key !== '__samples')
      .map(([id, count]) => `${id}=${count}`)
      .join(', ');
    console.log(`- ${entry.file}: ${entry.total}${detail ? ` (${detail})` : ''}`);
  }

  if (entries.length === 0) {
    console.log('- none');
  }
}

function mergeRankedEntries(entries) {
  const byFile = new Map();

  for (const entry of entries) {
    const existing = byFile.get(entry.file) || { file: entry.file, total: 0, counts: {} };
    for (const [id, count] of Object.entries(entry.counts)) {
      if (id === '__samples') continue;
      existing.counts[id] = (existing.counts[id] || 0) + count;
    }
    existing.total = Object.values(existing.counts).reduce((sum, count) => sum + count, 0);
    byFile.set(entry.file, existing);
  }

  return [...byFile.values()].sort((a, b) => b.total - a.total || a.file.localeCompare(b.file));
}

function parseArgValue(flag) {
  for (let index = 0; index < process.argv.length; index += 1) {
    const arg = process.argv[index];

    if (arg === flag) return process.argv[index + 1] || '';
    if (arg.startsWith(`${flag}=`)) return arg.slice(flag.length + 1);
  }

  return '';
}

function readThresholdBaseline(path) {
  const empty = { path: '', informational: {}, protectedAssistant: {}, errors: [] };
  if (!path) return empty;

  try {
    const parsed = JSON.parse(readFileSync(join(root, path), 'utf8'));

    return {
      path,
      informational: parsed.informational || {},
      protectedAssistant: parsed.protectedAssistant || {},
      errors: [],
    };
  } catch (error) {
    return {
      ...empty,
      path,
      errors: [`Unable to read audit baseline "${path}": ${error.message}`],
    };
  }
}

function parseThresholdArgs(flag, checkList, options = {}) {
  const { baselineThresholds = {}, baselineErrors = [] } = options;
  const knownIds = new Set(checkList.map((check) => check.id));
  const thresholds = {};
  const errors = [...baselineErrors];
  let enabled = baselineErrors.length > 0;
  let explicitFlagSeen = false;
  let explicitValueSeen = false;

  for (const [id, rawLimit] of Object.entries(baselineThresholds)) {
    enabled = true;
    const limit = Number(rawLimit);

    if (!knownIds.has(id)) {
      errors.push(`Unknown check "${id}" in --baseline for ${flag}.`);
      continue;
    }

    if (!Number.isInteger(limit) || limit < 0) {
      errors.push(`Invalid baseline limit "${rawLimit}" for check "${id}".`);
      continue;
    }

    thresholds[id] = limit;
  }

  for (let index = 0; index < process.argv.length; index += 1) {
    const arg = process.argv[index];
    let value = null;

    if (arg === flag) {
      value = process.argv[index + 1] || '';
      explicitFlagSeen = true;
      enabled = true;
    } else if (arg.startsWith(`${flag}=`)) {
      value = arg.slice(flag.length + 1);
      explicitFlagSeen = true;
      enabled = true;
    }

    if (!value) continue;
    explicitValueSeen = true;

    for (const pair of value.split(',')) {
      if (!pair.trim()) continue;

      const [id, rawLimit] = pair.split(/[=:]/);
      const limit = Number(rawLimit);

      if (!knownIds.has(id)) {
        errors.push(`Unknown check "${id}" in ${flag}.`);
        continue;
      }

      if (!Number.isInteger(limit) || limit < 0) {
        errors.push(`Invalid limit "${rawLimit}" for check "${id}".`);
        continue;
      }

      thresholds[id] = limit;
    }
  }

  if (explicitFlagSeen && !explicitValueSeen) {
    return { enabled: false, thresholds: {}, errors: [] };
  }

  return { enabled, thresholds, errors };
}

function thresholdFailures(totals, thresholdConfig, options = {}) {
  const { defaultLimit = null } = options;
  const entries = defaultLimit === null
    ? Object.entries(thresholdConfig.thresholds)
    : Object.entries(totals).map(([id, actual]) => [id, thresholdConfig.thresholds[id] ?? defaultLimit, actual]);

  return entries
    .map(([id, limit, actual]) => ({ id, actual: actual ?? totals[id] ?? 0, limit }))
    .filter((entry) => entry.actual > entry.limit);
}

function mergeTotals(...totalsList) {
  return Object.assign({}, ...totalsList);
}

function nonzeroTotals(totals) {
  return Object.fromEntries(Object.entries(totals).filter(([, count]) => count > 0));
}

function compactEntries(entries) {
  return entries.map(({ file, total, counts }) => ({ file, total, counts }));
}

function sumTotals(totals) {
  return Object.values(totals).reduce((sum, count) => sum + count, 0);
}

function buildBaselineReport() {
  return {
    informational: informational.totals,
    protectedAssistant: nonzeroTotals(allProtectedAssistantTotals),
  };
}

function buildJsonReport(options = {}) {
  const { includeSamples = true } = options;
  const serializeEntries = includeSamples ? (entries) => entries : compactEntries;
  const serializeChecks = (checkList) => checkList.map(({ id, label }) => ({ id, label }));
  const targetMode = changedMode ? 'changed' : pathFilters.length > 0 || explicitPathMode ? 'paths' : 'targets';

  return {
    metadata: {
      strictMode: strict,
      strictEnforcedExcludesProtectedAssistantUi: true,
      targetMode,
      targets,
      targetPaths: targetMode === 'changed' ? changed.paths : pathFilters,
      targetErrors,
      filesScanned: files.length,
      protectedAssistantFiles: [...protectedUiFiles.keys()],
      samplesIncluded: includeSamples,
      thresholdBaseline: thresholdBaseline.path,
      informationalThresholds: informationalThresholds.thresholds,
      protectedAssistantThresholds: protectedAssistantThresholds.thresholds,
    },
    checks: {
      strict: serializeChecks(checks),
      advisory: serializeChecks(advisoryChecks),
      informational: serializeChecks(informationalChecks),
    },
    strict: {
      totals,
      protectedAssistantTotals: protectedTotals,
      topFiles: serializeEntries(rankedFiles),
      protectedAssistantTopFiles: serializeEntries(protectedRankedFiles),
    },
    advisory: {
      totals: advisory.totals,
      protectedAssistantTotals: advisory.protectedTotals,
      topFiles: serializeEntries(advisory.rankedFiles),
      protectedAssistantTopFiles: serializeEntries(advisory.protectedRankedFiles),
    },
    informational: {
      totals: informational.totals,
      protectedAssistantTotals: informational.protectedTotals,
      topFiles: serializeEntries(informational.rankedFiles),
      protectedAssistantTopFiles: serializeEntries(informational.protectedRankedFiles),
      thresholdErrors: informationalThresholds.errors,
      thresholdFailures: informationalFailures,
    },
    protectedAssistantThreshold: {
      errors: protectedAssistantThresholds.errors,
      failures: protectedAssistantFailures,
    },
  };
}

const changed = changedMode ? collectChangedFiles() : { files: [], paths: [], errors: [] };
const focused = collectFocusedFiles(pathFilters);
const targetErrors = [...changed.errors, ...focused.errors];
const files = changedMode
  ? changed.files
  : pathFilters.length > 0 || explicitPathMode
  ? focused.files
  : targets.flatMap((target) => walk(join(root, target)));
const { totals, rankedFiles, protectedTotals, protectedRankedFiles } = collect(checks);
const advisory = collect(advisoryChecks);
const informational = collect(informationalChecks);
const allProtectedAssistantTotals = mergeTotals(protectedTotals, advisory.protectedTotals, informational.protectedTotals);
const informationalFailures = thresholdFailures(informational.totals, informationalThresholds);
const protectedAssistantFailures = protectedAssistantThresholds.enabled
  ? thresholdFailures(allProtectedAssistantTotals, protectedAssistantThresholds, { defaultLimit: 0 })
  : [];
const hasStrictFailures = rankedFiles.length > 0 || advisory.rankedFiles.length > 0;
const hasInformationalThresholdFailures = informationalThresholds.errors.length > 0 || informationalFailures.length > 0;
const hasProtectedAssistantThresholdFailures = protectedAssistantThresholds.errors.length > 0 || protectedAssistantFailures.length > 0;
const hasTargetErrors = targetErrors.length > 0;

if (printBaseline) {
  await new Promise((resolve) => {
    process.stdout.write(`${JSON.stringify(buildBaselineReport(), null, 2)}\n`, resolve);
  });
  process.exit(0);
}

if (json) {
  const payload = JSON.stringify(buildJsonReport({ includeSamples: !summary }), null, 2);

  await new Promise((resolve) => {
    process.stdout.write(`${payload}\n`, resolve);
  });
  process.exit(
    (strict && hasStrictFailures) || hasInformationalThresholdFailures || hasProtectedAssistantThresholdFailures || hasTargetErrors
      ? 1
      : 0,
  );
}

if (
  quiet &&
  !(strict && hasStrictFailures) &&
  !hasInformationalThresholdFailures &&
  !hasProtectedAssistantThresholdFailures &&
  !hasTargetErrors
) {
  const productStrictCount = rankedFiles.reduce((sum, entry) => sum + entry.total, 0);
  const informationalCount = sumTotals(informational.totals);
  const protectedAssistantCount = sumTotals(allProtectedAssistantTotals);

  console.log(
    [
      'Design audit passed',
      `productStrict=${productStrictCount}`,
      `informational=${informationalCount}`,
      `protectedAssistant=${protectedAssistantCount}`,
      thresholdBaseline.path ? `baseline=${thresholdBaseline.path}` : null,
    ].filter(Boolean).join(' '),
  );
  process.exit(0);
}

if (queue) {
  const targetMode = changedMode ? 'changed files' : pathFilters.length > 0 || explicitPathMode ? 'focused paths' : 'all targets';

  console.log('Design audit migration queue');
  console.log(`Scope: ${targetMode}`);
  if (changedMode) {
    console.log(`Changed paths: ${changed.paths.length ? changed.paths.join(', ') : '(no changed app/components files)'}`);
  } else if (pathFilters.length > 0 || explicitPathMode) {
    console.log(`Focused paths: ${pathFilters.length ? pathFilters.join(', ') : '(none)'}`);
  }
  console.log(`Strict product drift: ${rankedFiles.reduce((sum, entry) => sum + entry.total, 0)}`);
  console.log(`Advisory product drift: ${advisory.rankedFiles.reduce((sum, entry) => sum + entry.total, 0)}`);
  console.log(`Informational product candidates: ${sumTotals(informational.totals)}`);
  console.log(`Protected assistant reported only: ${sumTotals(allProtectedAssistantTotals)}`);
  console.log('');
  console.log('Next non-assistant product files:');
  printQueueEntries(informational.rankedFiles);
  console.log('');
  console.log('Protected assistant files, do not migrate without focused assistant QA:');
  printQueueEntries(mergeRankedEntries([...protectedRankedFiles, ...advisory.protectedRankedFiles, ...informational.protectedRankedFiles]));

  process.exit(
    (strict && hasStrictFailures) || hasInformationalThresholdFailures || hasProtectedAssistantThresholdFailures || hasTargetErrors
      ? 1
      : 0,
  );
}

console.log('Design system drift report');
console.log('Strict-enforced product surfaces exclude explicitly protected assistant UI files.');
if (changedMode) {
  console.log(`Changed-file scan: ${changed.paths.length ? changed.paths.join(', ') : '(no changed app/components files)'}`);
} else if (pathFilters.length > 0 || explicitPathMode) {
  console.log(`Focused path scan: ${pathFilters.length ? pathFilters.join(', ') : '(none)'}`);
}
console.log('');
for (const check of checks) {
  console.log(`${check.label}: ${totals[check.id]}`);
}

console.log('');
console.log('Top files:');
printRankedEntries(rankedFiles, checks, { includeSamples: details });

console.log('');
console.log('Protected assistant UI drift, reported only:');
printRankedEntries(protectedRankedFiles, checks, { includeSamples: details });

console.log('');
console.log('Advisory primitives migration map:');
for (const check of advisoryChecks) {
  console.log(`${check.label}: ${advisory.totals[check.id]}`);
}

console.log('');
console.log('Advisory top files:');
printRankedEntries(advisory.rankedFiles, advisoryChecks, { includeSamples: details });

console.log('');
console.log('Protected assistant UI advisory drift, reported only:');
printRankedEntries(advisory.protectedRankedFiles, advisoryChecks, { includeSamples: details });

console.log('');
console.log('Informational primitive migration candidates, reported only:');
for (const check of informationalChecks) {
  console.log(`${check.label}: ${informational.totals[check.id]}`);
}

console.log('');
console.log('Informational top files:');
printRankedEntries(informational.rankedFiles, informationalChecks, { includeSamples: details });

console.log('');
console.log('Protected assistant UI informational candidates, reported only:');
printRankedEntries(informational.protectedRankedFiles, informationalChecks, { includeSamples: details });

if (targetErrors.length > 0) {
  console.error('');
  console.error('Design audit target selection failed.');

  for (const error of targetErrors) {
    console.error(`- ${error}`);
  }

  process.exit(1);
}

if (strict && (rankedFiles.length > 0 || advisory.rankedFiles.length > 0)) {
  console.error('');
  console.error('Design audit strict mode failed.');
  process.exit(1);
}

if (informationalThresholds.errors.length > 0 || informationalFailures.length > 0) {
  console.error('');
  console.error('Design audit informational thresholds failed.');

  for (const error of informationalThresholds.errors) {
    console.error(`- ${error}`);
  }

  for (const failure of informationalFailures) {
    console.error(`- ${failure.id}: ${failure.actual} exceeds ${failure.limit}`);
  }

  process.exit(1);
}

if (protectedAssistantThresholds.errors.length > 0 || protectedAssistantFailures.length > 0) {
  console.error('');
  console.error('Design audit protected assistant thresholds failed.');

  for (const error of protectedAssistantThresholds.errors) {
    console.error(`- ${error}`);
  }

  for (const failure of protectedAssistantFailures) {
    console.error(`- ${failure.id}: ${failure.actual} exceeds ${failure.limit}`);
  }

  process.exit(1);
}
