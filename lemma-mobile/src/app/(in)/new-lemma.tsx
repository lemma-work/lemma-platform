import { NewLemmaScreen } from "../../areas/more/begin/new-lemma";
import { ResumeInstall } from "../../areas/more/install/resume";

/** Starting a Lemma from the shelf; and an install somebody left, offered
 *  back above it. */
export default function NewLemma() {
    return <NewLemmaScreen top={<ResumeInstall />} />;
}
