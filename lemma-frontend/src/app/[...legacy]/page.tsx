import { notFound, redirect } from "next/navigation";
import { legacyAddress } from "@/site/legacy-address";
export const metadata = { robots: { index: false, follow: false } };
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ legacy: string[] }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const p = await params,
    q = await searchParams;
  const search = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v) search.set(k, Array.isArray(v) ? v[0] : v);
  });
  const target = legacyAddress(p.legacy, search);
  if (!target) notFound();
  redirect(target);
}
