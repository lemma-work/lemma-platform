export async function GET() {
    return Response.json({ configured: Boolean(process.env.TYPESAFE_API_KEY) }, { headers: { "Cache-Control": "no-store" } });
}
