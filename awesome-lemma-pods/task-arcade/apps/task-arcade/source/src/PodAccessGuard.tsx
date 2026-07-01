import { useEffect, useRef, useState } from "react";
import { useAuth, useCreateRecord, usePodAccess, useRecords } from "lemma-sdk/react";
import { client } from "./lemma";
import type { TeamMemberRow } from "./lemma";
import { Loader } from "./Loader";

const SHELL: React.CSSProperties = {
  display: "flex", alignItems: "center", justifyContent: "center",
  height: "100vh", background: "#bdeaf5", color: "#20362a",
  fontFamily: '"Avenir Next", "Trebuchet MS", sans-serif',
};

const FALLBACK_COLORS = ["#5bb0a6", "#d98a5b", "#c75b7a", "#7c9a3e", "#3f9ec0"];

function colorForEmail(email: string): string {
  let h = 0;
  for (let i = 0; i < email.length; i++) h = (h * 31 + email.charCodeAt(i)) >>> 0;
  return FALLBACK_COLORS[h % FALLBACK_COLORS.length];
}

type TeamState = "checking" | "empty" | "member" | "outsider";

export function PodAccessGuard({ children }: { children: React.ReactNode }) {
  const { user } = useAuth(client);
  const { status, hasAccess, isLoading, isRequestingAccess, error, requestAccess, refresh } =
    usePodAccess({ client, enabled: true, autoLoad: true });
  const joinedRef = useRef(false);
  const teamCheckRef = useRef(false);
  const [teamState, setTeamState] = useState<TeamState>("checking");

  const { records: teamMembers, refresh: refreshTeamMembers } = useRecords<TeamMemberRow>({
    client,
    tableName: "team_members",
    limit: 100,
    enabled: false,
  });

  const { create: createTeamMember } = useCreateRecord<TeamMemberRow>({
    client,
    tableName: "team_members",
  });

  useEffect(() => {
    if (status === "missing" && !joinedRef.current && !isRequestingAccess) {
      joinedRef.current = true;
      (async () => {
        try {
          await requestAccess();
          await refresh();
        } catch {
          joinedRef.current = false;
        }
      })();
    }
  }, [status, isRequestingAccess, requestAccess, refresh]);

  useEffect(() => {
    if (hasAccess && user?.email && !teamCheckRef.current) {
      teamCheckRef.current = true;
      (async () => {
        try {
          await refreshTeamMembers();
          const existing = teamMembers.find((m) => m.email === user.email);
          if (existing) {
            setTeamState("member");
            return;
          }
          if (teamMembers.length === 0) {
            const name = user.name ?? user.email.split("@")[0];
            await createTeamMember({
              name,
              email: user.email,
              role: "manager",
              color: colorForEmail(user.email),
            });
            await refreshTeamMembers();
            setTeamState("member");
          } else {
            setTeamState("outsider");
          }
        } catch {
          teamCheckRef.current = false;
        }
      })();
    }
  }, [hasAccess, user?.email, teamMembers, refreshTeamMembers, createTeamMember]);

  if (hasAccess && teamState === "member") return <>{children}</>;

  if (hasAccess && teamState === "outsider") {
    return (
      <div style={SHELL}>
        <div style={{ textAlign: "center", maxWidth: 380 }}>
          <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 10 }}>You're not on this team yet</div>
          <div style={{ fontSize: 14, color: "#66806d" }}>
            Ask the team's manager to invite you. Once they add you to the roster, refresh this page.
          </div>
        </div>
      </div>
    );
  }

  if (isLoading || isRequestingAccess || status === "checking" || (hasAccess && teamState === "checking")) {
    return <Loader label="Loading" />;
  }

  if (status === "pending") {
    return (
      <div style={SHELL}>
        <div style={{ textAlign: "center", maxWidth: 360 }}>
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Waiting for approval</div>
          <div style={{ fontSize: 14, color: "#66806d" }}>Your request to join has been submitted. You'll get access once it's approved.</div>
        </div>
      </div>
    );
  }

  if (error && status === "error") {
    return (
      <div style={SHELL}>
        <div style={{ textAlign: "center", maxWidth: 360 }}>
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Something went wrong</div>
          <div style={{ fontSize: 14, color: "#66806d" }}>{error.message}</div>
          <button onClick={() => refresh()} style={{ marginTop: 16, padding: "8px 16px", background: "#2f8d4d", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer" }}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  return null;
}
