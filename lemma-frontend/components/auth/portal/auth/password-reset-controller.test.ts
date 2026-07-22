import { describe, expect, it, vi } from "vitest";

import {
  PASSWORD_RESET_RESEND_COOLDOWN_MS,
  classifyPasswordResetFailure,
  passwordResetCooldownSeconds,
  passwordResetTokenFromSearch,
  runPasswordResetRequest,
  runPasswordResetSubmit,
  validateNewPasswords,
} from "@/components/auth/portal/auth/password-reset-controller";

describe("password reset controller", () => {
  it("preserves reset tokens while ignoring unrelated query values", () => {
    expect(
      passwordResetTokenFromSearch("?token=reset-token&tenantId=public&redirect_uri=%2Fhome"),
    ).toBe("reset-token");
    expect(passwordResetTokenFromSearch("?tenantId=public")).toBeNull();
  });

  it("enforces a short resend cooldown", () => {
    const sentAt = 10_000;
    expect(passwordResetCooldownSeconds(sentAt, sentAt)).toBe(
      PASSWORD_RESET_RESEND_COOLDOWN_MS / 1000,
    );
    expect(
      passwordResetCooldownSeconds(
        sentAt,
        sentAt + PASSWORD_RESET_RESEND_COOLDOWN_MS + 1,
      ),
    ).toBe(0);
  });

  it("validates password length and confirmation", () => {
    expect(validateNewPasswords("short", "short")).toBe(
      "Use at least 8 characters.",
    );
    expect(validateNewPasswords("strong-pass", "different-pass")).toBe(
      "The passwords do not match.",
    );
    expect(validateNewPasswords("strong-pass", "strong-pass")).toBeNull();
  });

  it("recognizes rate-limit failures without masking ordinary network errors", () => {
    expect(classifyPasswordResetFailure(new Error("Too many reset attempts"))).toBe(
      "rate-limited",
    );
    expect(classifyPasswordResetFailure(new Error("network unavailable"))).toBe(
      "error",
    );
  });

  it("renders the same confirmation for existing and unknown accounts", async () => {
    const existing = vi.fn().mockResolvedValue({ status: "OK" });
    const unknown = vi
      .fn()
      .mockResolvedValue({ status: "PASSWORD_RESET_NOT_ALLOWED" });

    await expect(runPasswordResetRequest(existing)).resolves.toEqual({
      status: "sent",
    });
    await expect(runPasswordResetRequest(unknown)).resolves.toEqual({
      status: "sent",
    });
  });

  it("preserves field errors and retryable server failures", async () => {
    await expect(
      runPasswordResetRequest(() =>
        Promise.resolve({
          status: "FIELD_ERROR",
          formFields: [{ error: "Enter a valid email." }],
        }),
      ),
    ).resolves.toEqual({
      status: "field-error",
      message: "Enter a valid email.",
    });

    const unavailable = new Error("verification service unavailable");
    await expect(
      runPasswordResetRequest(() => Promise.reject(unavailable)),
    ).rejects.toBe(unavailable);
  });

  it("maps successful, expired, and rejected password submissions", async () => {
    await expect(
      runPasswordResetSubmit(() => Promise.resolve({ status: "OK" })),
    ).resolves.toEqual({ status: "updated" });
    await expect(
      runPasswordResetSubmit(() =>
        Promise.resolve({ status: "RESET_PASSWORD_INVALID_TOKEN_ERROR" }),
      ),
    ).resolves.toEqual({ status: "invalid-token" });
    await expect(
      runPasswordResetSubmit(() =>
        Promise.resolve({
          status: "FIELD_ERROR",
          formFields: [{ error: "Choose a stronger password." }],
        }),
      ),
    ).resolves.toEqual({
      status: "field-error",
      message: "Choose a stronger password.",
    });
  });
});
