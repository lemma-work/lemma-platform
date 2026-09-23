import { useState } from 'react';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

interface UseFileUploadOptions {
    podId: string;
    directoryPath?: string;
    onSuccess?: (filePath: string) => void;
    onError?: (error: Error) => void;
}

export function useFileUpload({ podId, directoryPath = '/', onSuccess, onError }: UseFileUploadOptions) {
    const [isUploading, setIsUploading] = useState(false);
    const [progress, setProgress] = useState(0);

    const upload = async (file: File, path?: string) => {
        setIsUploading(true);
        setProgress(0);
        try {
            if (!podId.trim()) {
                throw new Error('podId is required to upload a pod file');
            }
            const result = await getLemmaClient(podId).files.upload(file, {
                directoryPath: path ?? directoryPath,
                name: file.name,
            });

            const filePath = result.path;

            if (onSuccess) onSuccess(filePath);
            return filePath;
        } catch (error) {
            console.error('Upload failed:', error);
            if (onError) onError(error instanceof Error ? error : new Error('Upload failed'));
            throw error;
        } finally {
            setIsUploading(false);
            setProgress(100);
        }
    };

    return {
        upload,
        isUploading,
        progress
    };
}
