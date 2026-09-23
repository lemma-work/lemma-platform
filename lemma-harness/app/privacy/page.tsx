import type { Metadata } from 'next';
import { AnalyticsPreference } from '@/components/legal/analytics-preference';
import { LegalPage } from '@/components/legal/legal-page';
import { privacyPolicy } from '@/lib/data/legal';

export const metadata: Metadata = {
    // The heading reads "Privacy"; the tab and the search result want the
    // phrase people actually look for.
    title: 'Privacy Policy',
    description: privacyPolicy.description,
};

export default function PrivacyPage() {
    return <LegalPage document={privacyPolicy} action={<AnalyticsPreference />} />;
}
