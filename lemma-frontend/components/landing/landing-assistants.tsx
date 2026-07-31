"use client";

import Image from "next/image";
import type { SurfaceMode } from "./landing-data";

/**
 * ChatGPT and Claude rendered close to the real desktop apps: ChatGPT light
 * with its project sidebar and in-app browser tab, Claude dark with its icon
 * rail and side panel. Both show ordinary assistant use over MCP, with the
 * Lemma app open beside the chat — and both stop at the same approval gate.
 */

function LemmaAppBody() {
  return (
    <div className="lp-appview">
      <header>
        <strong>Support Ops</strong>
        <span className="lp-appview-live">
          <i />
          Live
        </span>
      </header>

      <div className="lp-appview-row is-open">
        <span>
          <b>tkt_418</b>
          <small>Northwind · refund</small>
        </span>
        <em>$420</em>
        <span className="lp-appview-pill is-amber">needs approval</span>
      </div>

      <div className="lp-appview-approve">
        <p>Above the $250 threshold — an approver has to decide.</p>
        <span>
          <b>Approve</b>
          <i>Decline</i>
        </span>
      </div>

      <div className="lp-appview-row">
        <span>
          <b>tkt_417</b>
          <small>Halden · refund</small>
        </span>
        <em>$38</em>
        <span className="lp-appview-pill is-green">sent</span>
      </div>

      <div className="lp-appview-row">
        <span>
          <b>tkt_416</b>
          <small>Ravel · refund</small>
        </span>
        <em>$120</em>
        <span className="lp-appview-pill is-green">sent</span>
      </div>
    </div>
  );
}

function ChatGptSurface({ surface }: { surface: SurfaceMode }) {
  return (
    <div className="lp-gpt">
      <aside className="lp-gpt-side">
        <div className="lp-gpt-side-top">
          <span className="lp-gpt-brand">
            <Image src={surface.logos[0].src} alt="" width={15} height={15} />
            ChatGPT
            <i className="lp-gpt-caret" />
          </span>
          <i className="lp-gpt-search" />
        </div>

        <p className="lp-gpt-nav is-new">New chat</p>
        <p className="lp-gpt-nav">Sites</p>
        <p className="lp-gpt-nav">Scheduled</p>
        <p className="lp-gpt-nav">Plugins</p>

        <p className="lp-gpt-side-label">Projects</p>
        <p className="lp-gpt-nav is-folder">support-ops</p>
        <p className="lp-gpt-nav is-child is-current">Refund approvals</p>
        <p className="lp-gpt-nav is-child">Weekly summary</p>

        <div className="lp-gpt-side-user">
          <span>DA</span>
          Dana
        </div>
      </aside>

      <div className="lp-gpt-main">
        <header className="lp-gpt-thread-top">
          <span>Refund approvals</span>
          <i className="lp-gpt-dots" />
        </header>

        <div className="lp-gpt-thread">
          <p className="lp-gpt-user">Anything waiting on me in support?</p>

          <p className="lp-gpt-msg">
            Two refunds are waiting. Northwind is <code>$420</code>, which is
            over your approval threshold, so it needs a person. Opening it now.
          </p>

          <p className="lp-gpt-tool">
            <span>support-ops</span>
            refunds.list
          </p>

          <p className="lp-gpt-user">Approve the Northwind one.</p>

          <p className="lp-gpt-tool is-held">
            <span>support-ops</span>
            refund.approve
            <em>needs a person</em>
          </p>

          <p className="lp-gpt-msg">
            I am not allowed to approve that one — the pod reserves it for an
            approver. Use the button in the app and I will pick the work back
            up.
          </p>
        </div>

        <div className="lp-gpt-composer">
          <span className="lp-gpt-composer-input">Work with ChatGPT</span>
          <span className="lp-gpt-composer-row">
            <i className="lp-gpt-plus" />
            <em>Approve for me</em>
            <b>5.6 Sol High</b>
            <i className="lp-gpt-send" />
          </span>
        </div>
      </div>

      <div className="lp-gpt-browser">
        <div className="lp-gpt-tabs">
          <span className="lp-gpt-tab">
            <i />
            Support Ops
            <em>×</em>
          </span>
          <i className="lp-gpt-tab-new" />
        </div>
        <div className="lp-gpt-url">
          <i className="lp-gpt-nav-back" />
          <i className="lp-gpt-nav-fwd" />
          <span>support-ops.lemma.work</span>
        </div>
        <LemmaAppBody />
      </div>
    </div>
  );
}

function ClaudeSurface({ surface }: { surface: SurfaceMode }) {
  return (
    <div className="lp-cl">
      <aside className="lp-cl-rail">
        <i className="lp-cl-rail-logo" />
        <i className="lp-cl-rail-new" />
        <i />
        <i />
        <i />
        <span className="lp-cl-rail-user">D</span>
      </aside>

      <div className="lp-cl-main">
        <header className="lp-cl-top">
          <span>Refund approvals</span>
          <i className="lp-cl-caret" />
        </header>

        <div className="lp-cl-thread">
          <p className="lp-cl-msg">
            Two refunds are still waiting. Northwind at <b>$420</b> is over the
            approval threshold, so the pod reserves that decision for a person.
            I have opened it on the right.
          </p>

          <p className="lp-cl-tool">
            <span>support-ops</span>
            refunds.list
          </p>

          <p className="lp-cl-user">Approve the Northwind one.</p>

          <p className="lp-cl-tool is-held">
            <span>support-ops</span>
            refund.approve
            <em>needs a person</em>
          </p>

          <p className="lp-cl-msg">
            I cannot approve that one. Use the button in the app and I will pick
            the work back up from there.
          </p>
        </div>

        <div className="lp-cl-composer">
          <span className="lp-cl-composer-input">Reply to Claude…</span>
          <span className="lp-cl-composer-row">
            <i className="lp-cl-plus" />
            <b>Opus 4.8</b>
            <em>Extra</em>
            <i className="lp-cl-send" />
          </span>
        </div>

        <p className="lp-cl-disclaimer">
          Claude is AI and can make mistakes. Please double-check responses.
        </p>
      </div>

      <div className="lp-cl-panel">
        <header>
          <i className="lp-cl-panel-eye" />
          <i className="lp-cl-panel-code" />
          <span>
            Support Ops <em>· APP</em>
          </span>
          <b>Open</b>
          <i className="lp-cl-panel-x" />
        </header>
        <div className="lp-cl-panel-body">
          <Image
            src={surface.logos[0].src}
            alt=""
            width={0}
            height={0}
            className="lp-cl-panel-hidden"
          />
          <LemmaAppBody />
        </div>
      </div>
    </div>
  );
}

export function AssistantSurface({ surface }: { surface: SurfaceMode }) {
  return surface.key === "claude" ? (
    <ClaudeSurface surface={surface} />
  ) : (
    <ChatGptSurface surface={surface} />
  );
}
