/** Whether this server can carry a live call: the key of whichever voice model
 *  holds the microphone (`NEXT_PUBLIC_VOICE_PROVIDER`, Gemini unless it says
 *  gpt-live). A call button offered without it fails at the first word.
 *  Routing needs nothing here: the browser asks the backend's decisions API,
 *  which routes with or without a System One key. */
export async function GET() {
    const voice = process.env.NEXT_PUBLIC_VOICE_PROVIDER === "gpt-live" ? process.env.OPENAI_API_KEY : process.env.GEMINI_API_KEY;
    return Response.json({ configured: Boolean(voice) }, { headers: { "Cache-Control": "no-store" } });
}
