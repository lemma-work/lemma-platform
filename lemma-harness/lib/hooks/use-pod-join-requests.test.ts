import { describe, expect, it } from 'vitest';
import { ApiError } from 'lemma-sdk';

import { shouldRetryJoinRequestsFetch } from './use-pod-join-requests';

describe('join-requests retry policy', () => {
    it.each([401, 403, 404])('stops on %i, which is an answer', (statusCode) => {
        expect(
            shouldRetryJoinRequestsFetch(0, new ApiError(statusCode, 'Forbidden')),
        ).toBe(false);
    });

    it('stops when the pod says the role is insufficient', () => {
        expect(
            shouldRetryJoinRequestsFetch(
                0,
                new ApiError(403, 'Forbidden', 'INSUFFICIENT_ROLE'),
            ),
        ).toBe(false);
    });

    it('retries a server error, which may well be a flake', () => {
        expect(shouldRetryJoinRequestsFetch(0, new ApiError(500, 'Boom'))).toBe(true);
    });

    it('gives up on a server error after two tries', () => {
        expect(shouldRetryJoinRequestsFetch(2, new ApiError(500, 'Boom'))).toBe(false);
    });
});
