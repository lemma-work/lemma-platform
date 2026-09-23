import Image from "next/image";
import type { SurfaceMode } from "./landing-data";
import { AssistantSurface } from "./landing-assistants";
import { PhoneSurface } from "./landing-phones";

export function SurfacePreview({ surface }: { surface: SurfaceMode }) {
  const isMail = surface.key === "email";
  const isPhone = surface.key === "telegram" || surface.key === "whatsapp";
  const isWorkspace = surface.key === "slack" || surface.key === "teams";
  const isAssistant = surface.key === "chatgpt" || surface.key === "claude";
  const sidebarItems =
    surface.key === "teams"
      ? ["Activity", "Approvals", "Campaigns", "Files"]
      : ["# approvals", "# support-triage", "# customer-escalations", "Apps"];

  if (isAssistant) {
    return (
      <aside
        className={`lp-surface-preview is-${surface.key}`}
        aria-label={`${surface.label} surface preview`}
      >
        <div className="lp-surface-window lp-assistant-window">
          <AssistantSurface surface={surface} />
        </div>
      </aside>
    );
  }

  if (isPhone) {
    return (
      <aside
        className={`lp-surface-preview is-${surface.key}`}
        aria-label={`${surface.label} surface preview`}
      >
        <div className="lp-surface-window lp-phone-window">
          <PhoneSurface surface={surface} />
        </div>
      </aside>
    );
  }

  if (isMail) {
    return (
      <aside
        className={`lp-surface-preview is-${surface.key}`}
        aria-label={`${surface.label} surface preview`}
      >
        <div className="lp-surface-window lp-mail-window">
          <header className="lp-surface-window-top">
            <span className="lp-surface-brand">
              <Image
                src={surface.logos[0].src}
                alt={surface.logos[0].label}
                width={24}
                height={24}
              />
              <strong>{surface.label}</strong>
            </span>
            <span className="lp-surface-search">Search customer threads</span>
            <Image
              className="lp-surface-avatar"
              src="/landing-page/slack-profile-avatar.jpg"
              alt=""
              width={30}
              height={30}
            />
          </header>
          <div className="lp-mail-window-body">
            <SurfaceEmailContent surface={surface} />
          </div>
        </div>
      </aside>
    );
  }

  if (!isWorkspace) {
    return (
      <aside
        className={`lp-surface-preview is-${surface.key}`}
        aria-label={`${surface.label} surface preview`}
      >
        <div className="lp-surface-window lp-api-window">
          <header className="lp-surface-window-top">
            <span className="lp-surface-brand">
              <Image
                src={surface.logos[0].src}
                alt={surface.logos[0].label}
                width={24}
                height={24}
              />
              <strong>API</strong>
            </span>
            <span className="lp-surface-search">support-ops.lemma.work</span>
            <Image
              className="lp-surface-avatar"
              src="/landing-page/slack-profile-avatar.jpg"
              alt=""
              width={30}
              height={30}
            />
          </header>
          <div className="lp-surface-thread">
            <div className="lp-surface-thread-head">
              <strong>POST /workflow.run</strong>
              <span>{surface.caption}</span>
            </div>
            <SurfaceApiContent />
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside
      className={`lp-surface-preview is-${surface.key}`}
      aria-label={`${surface.label} surface preview`}
    >
      <div className="lp-surface-window">
        <header className="lp-surface-window-top">
          <span className="lp-surface-brand">
            {surface.logos.map((logo) => (
              <Image
                key={logo.label}
                src={logo.src}
                alt={logo.label}
                width={24}
                height={24}
              />
            ))}
            <strong>{surface.label}</strong>
          </span>
          <span className="lp-surface-search">Search Lemma Ops</span>
          <Image
            className="lp-surface-avatar"
            src="/landing-page/slack-profile-avatar.jpg"
            alt=""
            width={30}
            height={30}
          />
        </header>

        <div className="lp-surface-window-body">
          <div className="lp-surface-sidebar" aria-hidden="true">
            {sidebarItems.map((item, index) => (
              <span className={index === 0 ? "is-selected" : ""} key={item}>
                {item}
              </span>
            ))}
          </div>

          <div className="lp-surface-thread">
            <div className="lp-surface-thread-head">
              <strong>@Lemma</strong>
              <span>{surface.caption}</span>
            </div>

            <SurfaceChatContent surface={surface} />
          </div>
        </div>
      </div>
    </aside>
  );
}

export function SurfaceChatContent({ surface }: { surface: SurfaceMode }) {
  const isTeams = surface.key === "teams";

  return (
    <div className="lp-surface-sequence">
      <div className="lp-surface-message is-human is-sequence-1">
        <Image
          src="/landing-page/slack-profile-avatar.jpg"
          alt=""
          width={34}
          height={34}
        />
        <p>
          <strong>{isTeams ? "Ava" : "Dana"}</strong>{" "}
          {isTeams
            ? "Review Q3 campaign spend. Ask before pausing."
            : "Anything waiting on me?"}
        </p>
      </div>
      <div className="lp-surface-message is-lemma is-sequence-2">
        <span>Le</span>
        <p>
          <strong>Lemma</strong>{" "}
          {isTeams
            ? "Checked spend, goals, and permissions. One pause needs approval."
            : "One approval. Northwind crossed the ICP threshold. Quill scored it 87."}
        </p>
      </div>
      <article className="lp-surface-approval is-sequence-3">
        <div>
          <span className="lp-surface-warning">!</span>
          <strong>
            {isTeams ? "Budget review needed" : "Approval needed"}
          </strong>
          <em>{isTeams ? "Quarterly campaign" : "Lead routing"}</em>
        </div>
        <p>
          Lemma checked policy and queued the decision in this{" "}
          {isTeams ? "workspace" : "channel"}.
        </p>
        <blockquote>
          {isTeams
            ? "Pause spend above threshold until Monday and notify finance."
            : "Route Northwind to the enterprise team and assign an owner."}
        </blockquote>
        <div className="lp-surface-actions">
          <span className="is-primary">Approve</span>
          <span>Revise</span>
          <span>Escalate</span>
        </div>
      </article>

      <div className="lp-surface-message is-human is-sequence-4">
        <Image
          src="/landing-page/slack-profile-avatar.jpg"
          alt=""
          width={34}
          height={34}
        />
        <p>
          <strong>{isTeams ? "Ava" : "Dana"}</strong> Approve
        </p>
      </div>
      <div className="lp-surface-message is-lemma is-sequence-5">
        <span>Le</span>
        <p>
          <strong>Lemma</strong>{" "}
          {isTeams
            ? "Done. Spend paused, finance notified, log updated."
            : "Done. Northwind routed, owner assigned, record updated. Same state everywhere."}
        </p>
      </div>
    </div>
  );
}

export function SurfaceEmailContent({ surface }: { surface: SurfaceMode }) {
  return (
    <div className="lp-email-surface">
      <nav className="lp-email-rail" aria-hidden="true">
        {["G", "C", "D", "M"].map(
          (item, index) => (
            <span className={index === 0 ? "is-active" : ""} key={item}>
              {item}
            </span>
          ),
        )}
      </nav>
      <div className="lp-email-list" aria-hidden="true">
        <strong>Primary</strong>
        {[
          [
            "Support escalation",
            "Refund exception request",
            "Needs approval",
          ],
          ["Operations", "Customer record changed after reply", "Synced"],
          ["Finance Team", "Friday reminder queued for owner", "Scheduled"],
          [
            "Customer Success",
            "Implementation follow-up",
            "Logged",
          ],
        ].map(([from, subject, status], index) => (
          <article className={index === 0 ? "is-selected" : ""} key={subject}>
            <span>
              <strong>{from}</strong>
              <small>{subject}</small>
            </span>
            <em>{status}</em>
          </article>
        ))}
      </div>
      <div className="lp-email-reading-pane">
        <article className="lp-email-card">
          <div>
            <span className="lp-email-avatar">CS</span>
            <span>
              <strong>Refund exception request</strong>
              <small>Received 9:15 AM</small>
            </span>
          </div>
          <p>
            Priority customer is asking for a one-time exception after a run of
            implementation issues.
          </p>
        </article>
        <div className="lp-surface-compose">
          <div>
            <Image src={surface.logos[0].src} alt="" width={24} height={24} />
            <strong>Lemma draft ready</strong>
          </div>
          <p>
            The exception can be approved once, with a follow-up task opened for
            the implementation issue.
          </p>
          <span>Send the reply</span>
        </div>
        <div className="lp-email-sync-list" aria-hidden="true">
          {[
            ["approval.status", "waiting"],
            ["customer.record", "ready to update"],
            ["follow_up.owner", "CSM"],
          ].map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function SurfaceApiContent() {
  return (
    <div className="lp-api-surface">
      <div className="lp-api-call">
        <span>POST</span>
        <strong>/pods/support-ops/workflows/refund-review/run</strong>
      </div>
      <pre aria-label="API request body">
        <code>
          {
            '{\n  "customer_id": "cus_82914",\n  "amount": 420,\n  "channel": "app"\n}'
          }
        </code>
      </pre>
      {[
        ["input.customer_id", "cus_82914"],
        ["workflow.status", "waiting_for_approval"],
        ["agent.draft_reply", "ready"],
        ["table.customers.updated", "true"],
      ].map(([key, value]) => (
        <div className="lp-api-row" key={key}>
          <span>{key}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}
