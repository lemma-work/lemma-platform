'use client';

import { use, useState } from 'react';
import { useRouter } from 'next/navigation';
import { FileCode, Plus } from 'lucide-react';
import { toast } from 'sonner';

import { FunctionEditor } from '@/components/functions/function-editor';
import { PodHeaderMetrics, PodPageHeader } from '@/components/pod/pod-page-header';
import { showResourceCreatedToast, showResourceErrorToast } from '@/components/shared/resource-feedback';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useCreateFunction } from '@/lib/hooks/use-functions';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { Function as FunctionType, FunctionStatus } from '@/lib/types';

type FunctionPanelTab = 'code' | 'config' | 'schemas' | 'runs';

export default function NewFunctionPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const router = useRouter();
    const podAccess = usePodAccess(podId);
    const createFunction = useCreateFunction();
    const [panelTab, setPanelTab] = useState<FunctionPanelTab>('code');

    const [localData, setLocalData] = useState<Partial<FunctionType>>({
        name: 'untitled_function',
        icon_url: null,
        code: `#input_type_name: FunctionInput
#output_type_name: FunctionOutput
#function_name: untitled_function

from pydantic import BaseModel
from lemma_sdk import FunctionContext


class FunctionInput(BaseModel):
    message: str = ""


class FunctionOutput(BaseModel):
    result: str


async def untitled_function(ctx: FunctionContext, data: FunctionInput) -> FunctionOutput:
    return FunctionOutput(result=f"Processed: {data.message}")
`,
        config: null,
        input_schema: {},
        output_schema: {},
        status: FunctionStatus.DRAFT,
        visibility: 'POD',
    });

    const handleUpdate = (updates: Partial<FunctionType>) => {
        setLocalData((prev) => ({ ...prev, ...updates }));
    };

    const handleCreate = async () => {
        if (!podAccess.can('function.create')) return;
        if (!localData.name?.trim()) {
            toast.error('Please enter a function name');
            return;
        }

        try {
            const newFunction = await createFunction.mutateAsync({
                podId,
                data: {
                    name: localData.name,
                    icon_url: localData.icon_url || undefined,
                    code: localData.code,
                    config: localData.config,
                    input_schema: localData.input_schema,
                    output_schema: localData.output_schema,
                    visibility: localData.visibility as never,
                },
            });

            showResourceCreatedToast('Function', newFunction.name);
            router.push(`/pod/${podId}/functions/${encodeURIComponent(newFunction.name)}?created=function`);
        } catch (error) {
            console.error('Failed to create function:', error);
            showResourceErrorToast(error, 'Failed to create function');
        }
    };

    if (!podAccess.isLoading && !podAccess.can('function.create')) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent px-4">
                <div className="surface-panel max-w-lg p-6 text-center sm:p-8">
                    <h2 className="mb-2 font-display text-xl font-semibold text-[var(--text-primary)]">No access to create functions</h2>
                    <p className="text-sm text-[var(--text-secondary)]">You can use the function area, but creating functions is outside your current permissions.</p>
                </div>
            </div>
        );
    }

    return (
        <div className="h-full flex flex-col bg-transparent">
            <PodPageHeader
                podId={podId}
                variant="bar"
                title={localData.name || 'Untitled Function'}
                eyebrow="Function builder"
                backHref={`/pod/${podId}/functions`}
                backLabel="Functions"
                icon={<FileCode className="h-3.5 w-3.5" />}
                meta={<PodHeaderMetrics items={[
                    { label: 'Status', value: (localData.name || '').trim() ? 'Ready to create' : 'Draft', tone: (localData.name || '').trim() ? 'ready' : 'muted' },
                    { label: 'Panel', value: panelTab },
                ]} />}
                tabs={(
                        <Tabs value={panelTab} onValueChange={(value) => setPanelTab(value as FunctionPanelTab)} className="min-w-0">
                            <TabsList className="lemma-header-tabs flex-nowrap">
                                <TabsTrigger value="code" className="lemma-header-tab">Code</TabsTrigger>
                                <TabsTrigger value="config" className="lemma-header-tab">Config</TabsTrigger>
                                <TabsTrigger value="schemas" className="lemma-header-tab">Schemas</TabsTrigger>
                                <TabsTrigger value="runs" className="lemma-header-tab">Runs</TabsTrigger>
                            </TabsList>
                        </Tabs>
                )}
                actions={(
                    <Button
                        onClick={handleCreate}
                        disabled={createFunction.isPending}
                        size="sm"
                        className="gap-2"
                    >
                        <Plus className="h-3.5 w-3.5" />
                        {createFunction.isPending ? 'Creating…' : 'Create Function'}
                    </Button>
                )}
            />

            <div className="flex-1 overflow-hidden">
                <FunctionEditor
                    podId={podId}
                    functionData={localData as FunctionType}
                    panelTab={panelTab}
                    onPanelTabChange={setPanelTab}
                    onUpdate={handleUpdate}
                    isUpdating={false}
                    hideHeader
                    isNameEditable
                    shareUrl={undefined}
                />
            </div>
        </div>
    );
}
