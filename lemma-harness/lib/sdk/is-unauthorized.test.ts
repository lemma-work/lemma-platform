import { describe, expect, it } from 'vitest';
import { isUnauthorized } from './is-unauthorized';

describe('isUnauthorized', () => {
    it('recognises the SDK rejection that must not be retried', () => {
        // The shape ApiError/UnauthorizedError actually carries.
        expect(isUnauthorized({ name: 'UnauthorizedError', statusCode: 401 })).toBe(true);
    });

    it('recognises a 401 whose name did not survive the package boundary', () => {
        // Why the check is structural: a duplicated copy of the SDK in the
        // module graph gives an error that is a different class with the same
        // status. Matching on `instanceof` alone would put the storm back.
        expect(isUnauthorized({ statusCode: 401 })).toBe(true);
        expect(isUnauthorized({ name: 'UnauthorizedError' })).toBe(true);
    });

    it('leaves every other rejection retryable', () => {
        // 403 is an authenticated user meeting a permission denial — not worth
        // retrying either, but not this, and the SDK draws the line in the same
        // place. 429 and 5xx are exactly what the retry budget exists for.
        expect(isUnauthorized({ name: 'ForbiddenError', statusCode: 403 })).toBe(false);
        expect(isUnauthorized({ name: 'RateLimitError', statusCode: 429 })).toBe(false);
        expect(isUnauthorized({ name: 'ServerError', statusCode: 503 })).toBe(false);
        expect(isUnauthorized({ name: 'NetworkError' })).toBe(false);
    });

    it('does not throw on the things a rejection can also be', () => {
        expect(isUnauthorized(undefined)).toBe(false);
        expect(isUnauthorized(null)).toBe(false);
        expect(isUnauthorized('401')).toBe(false);
        expect(isUnauthorized(401)).toBe(false);
        expect(isUnauthorized(new Error('boom'))).toBe(false);
    });

    it('does not treat a 401 written as a string as a match', () => {
        // The SDK types statusCode as a number; a string here means the object
        // came from somewhere else and the assumption behind skipping the retry
        // does not hold.
        expect(isUnauthorized({ statusCode: '401' })).toBe(false);
    });
});
