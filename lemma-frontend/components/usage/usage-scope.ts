export function usageOrganizationScope(
  conversation: { organization_id?: string | null } | undefined,
  fallback: string | null | undefined,
): string | null | undefined {
  // A loaded conversation's null organization is an explicit personal scope.
  return conversation ? conversation.organization_id ?? null : fallback;
}
