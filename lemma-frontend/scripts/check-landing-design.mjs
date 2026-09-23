import { readFile } from 'node:fs/promises';

// The landing has a fixed light palette. Validate it separately from the
// appearance-aware app tokens so this surface participates in every build.
const file = new URL('../src/app/(marketing)/landing.module.css', import.meta.url);
const css = (await readFile(file, 'utf8')).replace(/\/\*[\s\S]*?\*\//g, '');
const failures = [];
const tokens = Object.fromEntries([...css.matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\b/gi)].map(match => [match[1], match[2]]));

function luminance(hex) {
    const channels = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255)
        .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

for (const ink of ['--ink', '--ink-2', '--ink-3', '--wait', '--bad', '--ok']) {
    for (const surface of ['--cream', '--paper']) {
        if (!tokens[ink] || !tokens[surface]) {
            failures.push(`Missing fixed palette pair: ${ink} on ${surface}`);
            continue;
        }
        const values = [luminance(tokens[ink]), luminance(tokens[surface])].sort((a, b) => b - a);
        const contrast = (values[0] + 0.05) / (values[1] + 0.05);
        if (contrast < 4.5) failures.push(`${ink} on ${surface}: ${contrast.toFixed(2)}:1, requires 4.5:1`);
    }
}

css.split('\n').forEach((line, index) => {
    for (const size of line.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
        if (Number(size[1]) < 12) failures.push(`Line ${index + 1}: readable text must be at least 12px`);
    }
    if (/font-weight:\s*(?:[6-9]00|bold)/.test(line)) failures.push(`Line ${index + 1}: weight exceeds 500`);
    if (/(?:animation|transition):[^;}]*\b\d+(?:\.\d+)?m?s\b/.test(line)) failures.push(`Line ${index + 1}: use a shared motion token`);
    if (/(?<!-)color:\s*#(?:9a9b92|85867d|8b8c83|aeafa5|a8a99f|b9bab0)\b/i.test(line)) failures.push(`Line ${index + 1}: use the readable metadata ink token`);
});

if (failures.length) {
    console.error(failures.map(message => `landing design: ${message}`).join('\n'));
    process.exitCode = 1;
} else {
    console.log('landing design: 12 ink/surface pairs pass; type and motion tokens pass');
}
