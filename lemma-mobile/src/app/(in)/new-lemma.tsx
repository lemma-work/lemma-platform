import { NewLemmaScreen } from "../../areas/more/begin/new-lemma";
import { ResumeInstall } from "../../areas/more/install/resume";

/** Starting a Lemma: what it owns, or only a name, or a template; and an
 *  install somebody left, offered back above the starters. */
export default function NewLemma() {
    return <NewLemmaScreen top={<ResumeInstall />} />;
}
