/** Whether this server can carry a live call: the router's key, and the key
 *  of whichever voice model holds the microphone (`NEXT_PUBLIC_VOICE_PROVIDER`,
 *  Gemini unless it says gpt-live). Answering only for the router offered a
 *  call button that failed at the first word. */
export async function GET() {
    const voice = process.env.NEXT_PUBLIC_VOICE_PROVIDER === "gpt-live" ? process.env.OPENAI_API_KEY : process.env.GEMINI_API_KEY;
    const configured = Boolean(process.env.TYPESAFE_API_KEY) && Boolean(voice);
    return Response.json({ configured }, { headers: { "Cache-Control": "no-store" } });
}
