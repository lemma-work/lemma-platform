import type { ReactNode } from "react";
import p from "@/app/(marketing)/product.module.css";
import { faceSrc } from "../faces";
import l from "./landing.module.css";

/** A window of the plugin's app, framed the way the product pages frame one
 *  (`AppWindow`): its clay face and the view in the title bar. `fit` makes it
 *  as wide as what is in it, so a close-up keeps the size it was taken at. */
export function PluginWindow({ slug, title, fit = false, children }: { slug: string; title: string; fit?: boolean; children: ReactNode }) {
    return (
        <figure className={`${p.window} ${l.window} ${fit ? l.fit : ""}`}>
            <figcaption className={`${p.chrome} ${l.chrome}`}>
                <img src={faceSrc(slug)} alt="" width={22} height={22} />
                {title}
                <span className={p.dots} aria-hidden="true">
                    <i />
                    <i />
                    <i />
                </span>
            </figcaption>
            {children}
        </figure>
    );
}
