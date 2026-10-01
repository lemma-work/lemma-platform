import { notFound } from "next/navigation";
import { VIGNETTES } from "../../../vignettes";
import s from "../../../landing.module.css";

/** One vignette alone, for building and reviewing it beat by beat
 *  (`?beat=N` holds it on a beat). Development only. */
export default async function Preview({ params }: { params: Promise<{ id: string }> }) {
    if (process.env.NODE_ENV === "production") notFound();
    const Moment = VIGNETTES[(await params).id];
    if (!Moment) notFound();
    return <div className={s.page}><main style={{ padding: 40, maxWidth: 720 }}><Moment /></main></div>;
}
