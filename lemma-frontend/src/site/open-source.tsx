import Image from "next/image";
import { githubUrl } from "./links";
import s from "./open-source.module.css";
export function OpenSource() {
    return (
        <section
            className={s.section}
            id="open-source"
            aria-labelledby="open-source-title"
        >
            <div className={s.intro}>
                <p className={s.eyebrow}>OPEN SOURCE, FROM THE START</p>
                <h2 id="open-source-title">
                    Your work.
                    <br />
                    An open foundation.
                </h2>
                <p>
                    See how Lemma works. Run it on your own infrastructure. Help
                    shape what it becomes.
                </p>
                <a className={s.github} href={githubUrl}>
                    Explore the code on GitHub{" "}
                    <span aria-hidden="true">↗</span>
                </a>
                <div className={s.resources}>
                    <a href="/docs/getting-started">Self-host Lemma ↗</a>
                    <a href={githubUrl + "/blob/main/CONTRIBUTING.md"}>
                        Start contributing ↗
                    </a>
                </div>
                <small className={s.license}>
                    AGPLv3 core · Apache 2.0 SDKs
                </small>
            </div>
            <div className={s.visual}>
                <Image
                    src="/images/open-source-3d.png"
                    width={1536}
                    height={1024}
                    sizes="(max-width: 760px) 90vw, 50vw"
                    alt="Playful pink and green 3D teammates assembling colorful building blocks around the GitHub emblem."
                />
            </div>
        </section>
    );
}
