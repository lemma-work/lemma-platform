import { describe, expect, it } from 'vitest';
import {
    CRESTS,
    CREST_COUNT,
    FORMS,
    FORM_COUNT,
    FORM_DEPTH,
    GROUNDS,
    IDENTITY_BUCKETS,
    LEM_FORM,
    LEM_GENES,
    LEM_SEED,
    MOTION_PHASES,
    RESERVED_FORM_COUNT,
    STATE_LOOKS,
    TONE_COUNT,
    hashSeed,
    identityGenes,
    type IdentityState,
} from './seeded-identity';

describe('seeded identity', () => {
    it('draws the same avatar for the same seed', () => {
        expect(identityGenes('agent-42')).toEqual(identityGenes('agent-42'));
    });

    it('draws different avatars for different seeds', () => {
        expect(identityGenes('agent-42')).not.toEqual(identityGenes('agent-43'));
    });

    it('keeps every gene inside the range its table can index', () => {
        for (let index = 0; index < 500; index += 1) {
            const genes = identityGenes(`seed-${index}`);
            expect(genes.tone).toBeGreaterThanOrEqual(0);
            expect(genes.tone).toBeLessThan(TONE_COUNT);
            expect(FORMS[genes.form]).toBeTypeOf('string');
            expect(CRESTS[genes.crest]).toBeTypeOf('string');
            expect(GROUNDS[genes.ground]).toBeTypeOf('string');
            expect([0, 90, 180, 270]).toContain(genes.groundRotation);
        }
    });

    it('keeps the eyes inside the body', () => {
        for (let index = 0; index < 500; index += 1) {
            const { eyeSpacing, eyeY, eyeR } = identityGenes(`seed-${index}`);
            // Widest eye is the `wide` state at 1.24x, drawn at cx = 50 +/- spacing.
            const outerEdge = 50 + eyeSpacing + eyeR * 1.24;
            expect(outerEdge).toBeLessThan(96);
            expect(eyeY - eyeR * 1.12).toBeGreaterThan(28);
            expect(eyeY + eyeR * 1.12).toBeLessThan(94);
        }
    });

    it('has one depth overlay per form, reserved bodies included', () => {
        expect(FORMS).toHaveLength(FORM_COUNT + RESERVED_FORM_COUNT);
        expect(FORM_DEPTH).toHaveLength(FORM_COUNT + RESERVED_FORM_COUNT);
        expect(CRESTS).toHaveLength(CREST_COUNT);
    });

    it('never rolls a reserved body', () => {
        // The whole guarantee behind Lem's face: appended past the generator's
        // range rather than carved out of it, so no seed can reach it and no
        // agent loses a bucket to it.
        for (let index = 0; index < 2000; index += 1) {
            expect(identityGenes(`seed-${index}`).form).toBeLessThan(FORM_COUNT);
        }
    });

    it('draws the reserved creature for the reserved seed, and only for it', () => {
        expect(identityGenes(LEM_SEED)).toEqual(LEM_GENES);
        expect(LEM_GENES.form).toBe(LEM_FORM);
        expect(FORMS[LEM_GENES.form]).toBeTypeOf('string');
        expect(FORM_DEPTH[LEM_GENES.form]).toBeTypeOf('string');
        // Lem is the same creature in every pod — that is the point of it being
        // the responder you already know — so the seed must not be reachable by
        // a resource id or an agent name that happens to collide.
        expect(identityGenes('lem')).not.toEqual(LEM_GENES);
        expect(identityGenes('Lem')).not.toEqual(LEM_GENES);
    });

    it('keeps the reserved eyes inside the reserved body', () => {
        // The plinth carries its mass high and pinches at the waist, so the
        // seeded eye band (48-55) would have put the eyes on the neck.
        const outerEdge = 50 + LEM_GENES.eyeSpacing + LEM_GENES.eyeR * 1.24;
        expect(outerEdge).toBeLessThan(80);
        expect(LEM_GENES.eyeY + LEM_GENES.eyeR * 1.12).toBeLessThan(60);
    });

    it('starts every crest below the surface of every form', () => {
        // A crest that begins above the body it belongs to floats free of it —
        // which is exactly what the old ear and tab crests did once the capsule
        // and the rotated square joined the set. Anchoring them all at y=27 on
        // the centre line is the invariant that prevents it, so pin it here.
        for (const crest of CRESTS.slice(1)) {
            expect(crest).toMatch(/V?27/);
        }
    });

    it('reports the bucket count its factors actually produce', () => {
        expect(IDENTITY_BUCKETS).toBe(TONE_COUNT * FORM_COUNT * CREST_COUNT);
        expect(IDENTITY_BUCKETS).toBe(160);
    });

    it('never lets a state change the body colour', () => {
        // Identity is the body, state is the eyes and the pip. If a state ever
        // carried a tone, a failing agent would stop being recognisable as
        // itself at the moment you most need to recognise it.
        const looks = Object.values(STATE_LOOKS);
        for (const look of looks) {
            expect(JSON.stringify(look)).not.toMatch(/identity-tone/);
        }
    });

    it('distinguishes every state by eye shape as well as by pip colour', () => {
        // Colour alone would fail anyone who cannot separate the state hues.
        const states: IdentityState[] = ['idle', 'thinking', 'running', 'waiting', 'failed', 'asleep'];
        const shapes = states.map((state) => {
            const { kind, pupilX, pupilY, lid } = STATE_LOOKS[state].eye;
            return `${kind}:${pupilX}:${pupilY}:${lid}`;
        });
        // `idle` and `running` share an eye pose deliberately — a running agent
        // is told apart by the green pip and by breathing, not by a stare.
        expect(new Set(shapes).size).toBe(states.length - 1);
    });

    it('spreads a realistic roster across many buckets', () => {
        const names = Array.from({ length: 30 }, (_, index) => `agent-${index}`);
        const buckets = new Set(
            names.map((name) => {
                const genes = identityGenes(name);
                return `${genes.tone}|${genes.form}|${genes.crest}`;
            }),
        );
        // 30 draws into 160 buckets; theory puts twins near a sixth of the
        // roster, so anything below 22 distinct buckets means the generator has
        // stopped spreading and something upstream is clamping it.
        expect(buckets.size).toBeGreaterThanOrEqual(22);
    });

    it('scatters motion phases so a roster does not blink in unison', () => {
        // Regression: without a per-avatar phase every animation started at
        // mount, so a page of agents blinked on the same frame — which reads as
        // the page glitching rather than as a room of them.
        const phases = new Set(
            Array.from({ length: 40 }, (_, index) => identityGenes(`agent-${index}`).phase),
        );
        expect(phases.size).toBeGreaterThanOrEqual(MOTION_PHASES - 1);
    });

    it('hashes without collisions across a plausible pod', () => {
        const hashes = new Set(Array.from({ length: 2000 }, (_, i) => hashSeed(`resource-${i}`)));
        expect(hashes.size).toBe(2000);
    });
});
