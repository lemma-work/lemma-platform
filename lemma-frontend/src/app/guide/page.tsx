import { ChapterFive } from "@/site/guide/chapter-five";
import { ChapterFour } from "@/site/guide/chapter-four";
import { ChapterOne } from "@/site/guide/chapter-one";
import { ChapterSix } from "@/site/guide/chapter-six";
import { ChapterThree } from "@/site/guide/chapter-three";
import { ChapterTwo } from "@/site/guide/chapter-two";
import { GuideCover, GuideFoot, GuideIntro } from "@/site/guide/frame";
import { guideFace } from "@/site/guide/guide-face";
import { GuideHeader } from "@/site/guide/guide-header";
import { pageMetadata } from "@/site/metadata";

export const metadata = pageMetadata(
    "A factory behind every person",
    "Why AI still helps one person at a time, what it takes for it to do a team's work, and how Lemma is built around that.",
    "/guide",
);

/* The guide: what Lemma believes about working with AI, as one long read. It
   is written to be read from the top, so it is one page with a chapter per
   band rather than a page per chapter; the header's contents reach any
   section from anywhere. */
export default function Page() {
    return (
        <div className={`guidebook ${guideFace.variable}`}>
            <a className="site-skip" href="#content">
                Skip to content
            </a>
            <GuideHeader />
            <main id="content">
                <GuideCover />
                <GuideIntro />
                <ChapterOne />
                <ChapterTwo />
                <ChapterThree />
                <ChapterFour />
                <ChapterFive />
                <ChapterSix />
            </main>
            <GuideFoot />
        </div>
    );
}
