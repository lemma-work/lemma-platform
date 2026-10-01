import Image from "next/image";
import { FACES, type Picture, type Who } from "@/marketing/product-pages";
import p from "./product.module.css";

/** A capture of the real app: a still, or a short recording that plays while
 *  it is on screen (see `Lively`). The recording's poster is its first frame,
 *  so with no script or with reduced motion it is a still like the rest. */
export function Shot({ picture, sizes }: { picture: Picture; sizes: string }) {
    if (picture.kind === "still") {
        return <Image src={picture.src} width={picture.width} height={picture.height} sizes={sizes} alt={picture.alt} />;
    }
    const base = "/product/" + picture.name;
    return (
        <video className={p.loop} data-loop="" muted loop playsInline preload="metadata" poster={base + "-poster.webp"}
            width={picture.width} height={picture.height} aria-label={picture.alt}>
            <source src={base + ".webm"} type="video/webm" />
            <source src={base + ".mp4"} type="video/mp4" />
        </video>
    );
}

/** One window of the app, with whose it is in the title bar. */
export function AppWindow({ picture, title, who, sizes }: { picture: Picture; title: string; who?: Who; sizes: string }) {
    return (
        <figure className={p.window} data-rise="">
            <figcaption className={p.chrome}>
                {who && <img src={FACES[who]} alt="" width={22} height={22} />}
                {title}
                <span className={p.dots} aria-hidden="true"><i /><i /><i /></span>
            </figcaption>
            <Shot picture={picture} sizes={sizes} />
        </figure>
    );
}

/** A note in the margin, in a hand, with an arrow that draws itself in. */
export function Note({ children }: { children: string }) {
    return (
        <p className={p.note}>
            <span>{children}</span>
            <svg className={p.arrow} viewBox="0 0 120 70" fill="none" aria-hidden="true">
                <path pathLength={1} d="M6 8 C 30 50, 70 62, 110 56" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                <path pathLength={1} d="M98 46 L111 56 L97 64" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
        </p>
    );
}
