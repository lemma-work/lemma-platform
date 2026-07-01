#!/usr/bin/env node
/**
 * Seed script for Trumpet — sample data so the pod demos itself.
 *
 * Usage:
 *   node seed/seed.mjs                    # uses $LEMMA_POD_ID and $LEMMA_BASE_URL
 *   node seed/seed.mjs <pod-id>           # explicit pod id
 *
 * Prereqs:
 *   - lemma CLI installed + authenticated (lemma auth login)
 *   - Pod already created + bundle imported (lemma pods import .)
 *
 * Creates 4 people, 7 commitments, 3 habits, and 13 calendar events.
 * Idempotent — checks for existing people by name before creating.
 */
import { createClient } from 'lemma-sdk';

const POD_ID  = process.argv[2] || process.env.LEMMA_POD_ID || '';
const API_URL = process.env.LEMMA_BASE_URL || 'https://api.lemma.work';

if (!POD_ID) {
  console.error('Error: no pod id. Pass it as an argument or set LEMMA_POD_ID.');
  console.error('  node seed/seed.mjs <pod-id>');
  process.exit(1);
}

const client = createClient({ podId: POD_ID, apiUrl: API_URL });

console.log('Trumpet — seeding demo data for pod:', POD_ID);

// ── 1. Create people (idempotent — skip if already exists) ───────────────────
console.log('\n  Creating people...');

const people = [
  {
    name: 'Deepak Sharma',
    nickname: 'Deepak',
    role: 'Co-founder & CTO',
    organization: 'Lemma',
    photo_url: '/photos/deepak.jpg',
    notes: 'Technical co-founder. Builds infrastructure.',
  },
  {
    name: 'Ayush Gupta',
    nickname: 'Ayush',
    role: 'Design Lead',
    organization: 'Lemma',
    photo_url: '/photos/ayush.jpg',
    notes: 'Leads design and brand identity.',
  },
  {
    name: 'Sarah Chen',
    nickname: 'Sarah',
    role: 'Investor & Advisor',
    organization: 'Horizon Ventures',
    photo_url: '/photos/sarah.jpg',
    notes: 'Series A lead. Focused on enterprise SaaS.',
  },
  {
    name: 'Rahul Mehra',
    nickname: 'Rahul',
    role: 'Senior Engineer',
    organization: 'Lemma',
    photo_url: '/photos/rahul.jpg',
    notes: 'Backend & integrations. Owns the keeper agent.',
  },
];

// Check existing people to avoid duplicates
const existingPeople = await client.records.list('people', { limit: 500 });
const existingByName = new Map(
  (existingPeople.items || []).map(r => [r.name?.toLowerCase(), r.id])
);

const createdPeople = {};
for (const p of people) {
  const existingId = existingByName.get(p.name.toLowerCase());
  if (existingId) {
    createdPeople[p.nickname] = existingId;
    console.log(`    ✓ ${p.name} already exists → ${existingId.slice(0, 8)}`);
    continue;
  }
  const rec = await client.records.create('people', p);
  createdPeople[p.nickname] = rec.id;
  console.log(`    ✓ ${p.name} → ${rec.id}`);
}

// ── 2. Create commitments (to_others / from_others) ──────────────────────────
console.log('\n  Creating commitments...');

const toOthers = [
  {
    title: 'Share investor deck v3',
    description: 'Updated pitch deck with Q2 metrics and revised TAM slide',
    person_id: createdPeople['Sarah'],
    due_date: '2026-06-09',
    type: 'to_others',
    status: 'active',
    recurrence: 'none',
  },
  {
    title: "Review Rahul's API PR",
    description: 'Keeper integration pull request — needs approval before Friday deploy',
    person_id: createdPeople['Rahul'],
    due_date: '2026-06-10',
    type: 'to_others',
    status: 'active',
    recurrence: 'none',
  },
  {
    title: 'Send brand guidelines to Ayush',
    description: 'Typography rules and updated color tokens doc for the new design system',
    person_id: createdPeople['Ayush'],
    due_date: '2026-06-11',
    type: 'to_others',
    status: 'active',
    recurrence: 'none',
  },
  {
    title: 'Follow up on infra cost estimate',
    description: "Get Deepak's updated GCP cost breakdown for board meeting",
    person_id: createdPeople['Deepak'],
    due_date: '2026-06-12',
    type: 'to_others',
    status: 'active',
    recurrence: 'none',
  },
];

const fromOthers = [
  {
    title: 'Keeper agent changelog from Rahul',
    description: 'Release notes for v1.4 with new calendar + email integrations',
    person_id: createdPeople['Rahul'],
    due_date: '2026-06-09',
    type: 'from_others',
    status: 'active',
    recurrence: 'none',
  },
  {
    title: 'Figma export from Ayush',
    description: 'Final onboarding flow screens — ready for dev handoff',
    person_id: createdPeople['Ayush'],
    due_date: '2026-06-10',
    type: 'from_others',
    status: 'active',
    recurrence: 'none',
  },
  {
    title: 'Term sheet draft from Sarah',
    description: 'Bridge round terms — was promised by end of week',
    person_id: createdPeople['Sarah'],
    due_date: '2026-06-13',
    type: 'from_others',
    status: 'active',
    recurrence: 'none',
  },
];

for (const c of [...toOthers, ...fromOthers]) {
  const rec = await client.records.create('commitments', c);
  console.log(`    ✓ [${c.type}] "${c.title}" → ${rec.id}`);
}

// ── 3. Create habits ────────────────────────────────────────────────────────
console.log('\n  Creating habits...');

const habits = [
  {
    title: 'Morning standup prep',
    description: 'Review todos, check calendar, jot 3 priorities for the day',
    type: 'habit',
    preferred_time: '09:00',
    end_time: '09:20',
    recurrence: 'daily',
    status: 'active',
  },
  {
    title: 'Research & reading',
    description: 'Read one article or paper on AI, product, or design — no doomscrolling',
    type: 'habit',
    preferred_time: '20:00',
    end_time: '20:30',
    recurrence: 'daily',
    status: 'active',
  },
  {
    title: 'Weekly retrospective',
    description: 'What shipped, what blocked, what to carry into next week',
    type: 'habit',
    preferred_time: '17:00',
    end_time: '17:45',
    recurrence: 'weekly',
    status: 'active',
  },
];

for (const h of habits) {
  const rec = await client.records.create('commitments', h);
  console.log(`    ✓ [habit] "${h.title}" @ ${h.preferred_time} → ${rec.id}`);
}

// ── 4. Create calendar events ────────────────────────────────────────────────
console.log('\n  Creating calendar events...');

const calEvents = [
  { title: 'Lemma design sprint kickoff', description: 'Align on Q2 visual identity and component library approach', type: 'calendar', due_date: '2026-06-06', preferred_time: '10:00', end_time: '11:30', status: 'active', recurrence: 'none' },
  { title: 'Investor check-in — Sarah', description: 'Monthly 30-min catch-up — share metrics and open questions', type: 'calendar', due_date: '2026-06-06', preferred_time: '15:00', end_time: '15:30', status: 'active', recurrence: 'none' },
  { title: 'Keeper v1.4 planning', description: 'Scope the new integrations — gcal, gmail, slack handoff logic', type: 'calendar', due_date: '2026-06-07', preferred_time: '11:00', end_time: '12:00', status: 'active', recurrence: 'none' },
  { title: 'Design review — Trumpet home tab', description: 'Go through prototype with Ayush and align on spacing / typography', type: 'calendar', due_date: '2026-06-07', preferred_time: '14:00', end_time: '15:00', status: 'active', recurrence: 'none' },
  { title: 'Engineering standup', description: 'Weekly longer sync — blockers, deployments, infra status', type: 'calendar', due_date: '2026-06-08', preferred_time: '09:30', end_time: '10:00', status: 'active', recurrence: 'none' },
  { title: 'Product roadmap retro', description: 'Q2 retrospective — what shipped vs planned, carry-overs to Q3', type: 'calendar', due_date: '2026-06-08', preferred_time: '16:00', end_time: '17:00', status: 'active', recurrence: 'none' },
  { title: 'Daily standup', description: '3-min async summary → 10-min sync', type: 'calendar', due_date: '2026-06-09', preferred_time: '09:30', end_time: '09:45', status: 'active', recurrence: 'none' },
  { title: 'Trumpet data + UI wiring session', description: 'Seed real data, wire up people join, fix summary counts', type: 'calendar', due_date: '2026-06-09', preferred_time: '11:00', end_time: '13:00', status: 'active', recurrence: 'none' },
  { title: 'Sarah — fundraise strategy call', description: 'Discuss bridge round timing and term sheet next steps', type: 'calendar', due_date: '2026-06-09', preferred_time: '15:00', end_time: '15:45', status: 'active', recurrence: 'none' },
  { title: 'Keeper agent demo — team', description: 'Live demo of v1.4 integrations to the full team', type: 'calendar', due_date: '2026-06-10', preferred_time: '10:00', end_time: '11:00', status: 'active', recurrence: 'none' },
  { title: 'Deepak — infra review', description: 'GCP cost model review ahead of Series A due diligence', type: 'calendar', due_date: '2026-06-10', preferred_time: '14:30', end_time: '15:30', status: 'active', recurrence: 'none' },
  { title: 'Board prep session', description: 'Dry-run the board deck with Deepak before Thursday meeting', type: 'calendar', due_date: '2026-06-11', preferred_time: '09:00', end_time: '10:30', status: 'active', recurrence: 'none' },
  { title: 'Onboarding UX review', description: "Walk through Ayush's Figma export — dev handoff session", type: 'calendar', due_date: '2026-06-11', preferred_time: '13:00', end_time: '14:00', status: 'active', recurrence: 'none' },
];

for (const e of calEvents) {
  const rec = await client.records.create('commitments', e);
  console.log(`    ✓ [${e.due_date} ${e.preferred_time}] "${e.title}" → ${rec.id}`);
}

console.log('\nTrumpet — seed complete!');
console.log('   People:', Object.entries(createdPeople).map(([k,v]) => `${k}=${v.slice(0,8)}`).join(', '));
