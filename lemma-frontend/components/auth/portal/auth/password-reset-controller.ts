import { isRateLimitError } from "@/components/auth/portal/auth/verification-controller";

export const PASSWORD_RESET_RESEND_COOLDOWN_MS = 30_000;

export type PasswordResetFailure = "rate-limited" | "error";

type PasswordResetFieldError = {
  status: "FIELD_ERROR";
  formFields: Array<{ error: string }>;
};

type PasswordResetRequestResponse =
  | { status: "OK" | "PASSWORD_RESET_NOT_ALLOWED" }
  | PasswordResetFieldError;

type PasswordResetSubmitResponse =
  | { status: "OK" | "RESET_PASSWORD_INVALID_TOKEN_ERROR" }
  | PasswordResetFieldError;

export type PasswordResetRequestResult =
  | { status: "sent" }
  | { status: "field-error"; message: string };

export type PasswordResetSubmitResult =
  | { status: "updated" }
  | { status: "invalid-token" }
  | { status: "field-error"; message: string };

export async function runPasswordResetRequest(
  send: () => Promise<PasswordResetRequestResponse>,
): Promise<PasswordResetRequestResult> {
  const response = await send();
  if (response.status === "FIELD_ERROR") {
    return {
      status: "field-error",
      message: response.formFields[0]?.error || "Enter a valid email address.",
    };
  }
  // Both successful and non-existent-account responses intentionally render
  // the same state so account existence is never disclosed.
  return { status: "sent" };
}

export async function runPasswordResetSubmit(
  submit: () => Promise<PasswordResetSubmitResponse>,
): Promise<PasswordResetSubmitResult> {
  const response = await submit();
  if (response.status === "RESET_PASSWORD_INVALID_TOKEN_ERROR") {
    return { status: "invalid-token" };
  }
  if (response.status === "FIELD_ERROR") {
    return {
      status: "field-error",
      message: response.formFields[0]?.error || "Choose a stronger password.",
    };
  }
  return { status: "updated" };
}

export function passwordResetTokenFromSearch(search: string): string | null {
  const token = new URLSearchParams(search).get("token")?.trim();
  return token || null;
}

export function passwordResetCooldownSeconds(
  sentAt: number | null,
  now: number,
): number {
  if (sentAt === null) return 0;
  return Math.max(
    0,
    Math.ceil((sentAt + PASSWORD_RESET_RESEND_COOLDOWN_MS - now) / 1000),
  );
}

export function validateNewPasswords(
  password: string,
  confirmation: string,
): string | null {
  if (password.length < 8) return "Use at least 8 characters.";
  if (password !== confirmation) return "The passwords do not match.";
  return null;
}

export function classifyPasswordResetFailure(
  error: unknown,
): PasswordResetFailure {
  return isRateLimitError(error) ? "rate-limited" : "error";
}

export function passwordResetErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return "We couldn’t reach Lemma. Check your connection and try again.";
}
