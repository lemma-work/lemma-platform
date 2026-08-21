import { describe, expect, it } from 'vitest';
import { organizationSchema } from './structured-data';

describe('organizationSchema', () => {
    const schema = organizationSchema();

    it('is a typed, named Organization with a description', () => {
        expect(schema['@type']).toBe('Organization');
        expect(schema.name).toBe('Lemma');
        expect(typeof schema.description).toBe('string');
        expect((schema.description as string).length).toBeGreaterThan(0);
    });

    it('carries a contactPoint an agent can act on', () => {
        const contactPoint = schema.contactPoint as Record<string, unknown>;
        expect(contactPoint['@type']).toBe('ContactPoint');
        expect(contactPoint.contactType).toBe('customer support');
        expect(typeof contactPoint.email).toBe('string');
        expect((contactPoint.email as string).length).toBeGreaterThan(0);
    });
});
