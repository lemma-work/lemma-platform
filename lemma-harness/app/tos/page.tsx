import type { Metadata } from 'next';
import { LegalPage } from '@/components/legal/legal-page';
import { termsOfService } from '@/lib/data/legal';

export const metadata: Metadata = {
    title: 'Terms of Service',
    description: termsOfService.description,
};

export default function TermsOfServicePage() {
    return <LegalPage document={termsOfService} />;
}
