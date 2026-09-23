#!/usr/bin/env node
/**
 * Verifies the tour anchor contract: every [data-edu="..."] selector
 * referenced in lib/education/tours.ts must appear as a data-edu attribute
 * somewhere in app/ or components/. Run in CI or via `npm run check:edu-anchors`.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const toursSource = readFileSync(join(root, 'lib/education/tours.ts'), 'utf8');

const referenced = [...toursSource.matchAll(/\[data-edu="([a-z0-9-]+)"\]/g)].map((match) => match[1]);
if (referenced.length === 0) {
  console.error('check-edu-anchors: no anchors found in tours.ts — selector format changed?');
  process.exit(1);
}

const defined = new Set();
const walk = (dir) => {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      walk(path);
    } else if (/\.(tsx|ts|jsx|js)$/.test(entry)) {
      const source = readFileSync(path, 'utf8');
      for (const match of source.matchAll(/data-edu="([a-z0-9-]+)"/g)) {
        defined.add(match[1]);
      }
    }
  }
};
walk(join(root, 'app'));
walk(join(root, 'components'));

const missing = referenced.filter((anchor) => !defined.has(anchor));
if (missing.length > 0) {
  console.error(`check-edu-anchors: tours.ts references anchors with no matching data-edu attribute: ${missing.join(', ')}`);
  process.exit(1);
}

console.log(`check-edu-anchors: ${referenced.length} anchors verified.`);
