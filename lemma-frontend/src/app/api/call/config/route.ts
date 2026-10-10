/** Whether this server can carry a live call: the key of whichever voice model
 *  holds the microphone (`NEXT_PUBLIC_VOICE_PROVIDER`, Gemini unless it says
 *  gpt-live). Routing what the caller says is the backend's decisions API, not
 *  this server's; when the backend cannot answer, a call degrades to the voice
 *  alone rather than failing. */
export async function GET() {
    const voice = process.env.NEXT_PUBLIC_VOICE_PROVIDER === "gpt-live" ? process.env.OPENAI_API_KEY : process.env.GEMINI_API_KEY;
    return Response.json({ configured: Boolean(voice) }, { headers: { "Cache-Control": "no-store" } });
}
