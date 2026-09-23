'use client';

import { use } from 'react';

import { DocumentSpace } from '@/components/documents/document-space';

export default function FilesPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);

    return <DocumentSpace podId={podId} />;
}
