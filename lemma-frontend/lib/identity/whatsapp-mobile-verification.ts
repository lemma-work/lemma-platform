export const WHATSAPP_VERIFICATION_POLL_INTERVAL_MS = 5_000;

export function buildWhatsAppVerificationMessage(code: string): string {
  return `LEMMA VERIFY ${code.trim()}`;
}
