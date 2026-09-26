import { lemma } from "@/session/client";

/** Server setup's "Send a test email": the backend sends one to the signed-in
 *  person with the mail settings it is running with, so this tests what was
 *  saved, not the draft. Answers the sentence to show; throws that sentence
 *  when the email did not go. */
export async function sendTestEmail(send: () => Promise<{ ok: boolean; message: string }> = () => lemma().users.sendTestEmail()): Promise<string> {
    const answer = await send();
    if (!answer.ok) throw new Error(answer.message);
    return answer.message;
}
