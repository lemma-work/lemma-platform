'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';
import type { UserProfile } from '../types';

export const useProfile = () => {
    return useQuery({
        queryKey: ['user', 'profile'],
        queryFn: () => getLemmaClient().users.getProfile() as Promise<UserProfile>,
    });
};

export const useUpdateProfile = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: Partial<UserProfile>) => getLemmaClient().users.upsertProfile(data) as Promise<UserProfile>,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['user', 'profile'] });
        },
    });
};
