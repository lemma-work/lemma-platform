import "@/styles/outcomes.css";
import "@/styles/add-app.css";
import "@/styles/lemma-work.css";
import "@/styles/lemma-knows.css";
import "@/styles/people.css";
import "@/styles/connectors-page.css";
import "@/styles/apps-page.css";
import "@/styles/needs-page.css";
import "@/styles/frame-states.css";
import { productFace } from "@/ui/product-face";
import { Outcomes } from "./outcomes";

/* The app, under every one of its addresses -- `/home`, `/needs`, `/apps`,
   `/connectors`, `/people`, `/chats` and everything about a Lemma under
   `/l` -- mounted once here, beside pages that draw nothing: the frame reads
   its own address (`outcomes/address.ts`), so moving between them never
   crosses a boundary that would drop what its queries hold or reload an app
   it keeps open. Albert Sans is the product face; figures stay on Inter
   (`--font-ui`). */
export default function OutcomesLayout({ children }: { children: React.ReactNode }) {
    return <div className={productFace.variable}><Outcomes />{children}</div>;
}
