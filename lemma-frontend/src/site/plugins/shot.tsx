import Image from "next/image";
import type { Picture } from "./catalog";
import s from "./shot.module.css";

/** A real picture of a plugin's app, made from sample data, in the site's
 *  light or dark: both are in the page and the theme shows one, so it follows
 *  a visitor's own setting as well as their system's. A lazy picture that is
 *  hidden is never fetched; `eager` (a picture at the top of a page) fetches
 *  both, since either may be the one in view. */
export function Shot({ picture, alt, sizes, eager = false }: { picture: Picture; alt: string; sizes: string; eager?: boolean }) {
    const loading = eager ? "eager" : "lazy";
    return (
        <span className={s.shot} style={{ aspectRatio: `${picture.width} / ${picture.height}` }}>
            <Image className={s.light} src={picture.light} width={picture.width} height={picture.height} sizes={sizes} alt={alt} loading={loading} />
            <Image className={s.dark} src={picture.dark} width={picture.width} height={picture.height} sizes={sizes} alt={alt} loading={loading} />
        </span>
    );
}
