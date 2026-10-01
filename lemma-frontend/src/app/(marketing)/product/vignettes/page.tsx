import { notFound } from "next/navigation";
import { VIGNETTES } from "../../vignettes";
import { Lively } from "../../lively";
import s from "../../landing.module.css";

/** Every vignette on one page, for looking at them while they are made.
 *  Development only: in a production build this route is not there. */
export default function VignetteGallery() {
    if (process.env.NODE_ENV === "production") notFound();
    return (
        <div className={s.page}>
            <main className={s.wrap} style={{ padding: "64px 0", display: "grid", gap: 64, maxWidth: 760 }}>
                {Object.entries(VIGNETTES).map(([id, Vignette]) => (
                    <section key={id} id={id}>
                        <p className={s.label} style={{ marginBottom: 16 }}>{id}</p>
                        <Vignette />
                    </section>
                ))}
            </main>
            <Lively />
        </div>
    );
}
