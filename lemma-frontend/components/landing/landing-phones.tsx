"use client";

import Image from "next/image";
import type { SurfaceMode } from "./landing-data";

/**
 * Telegram and WhatsApp rendered close to the real clients.
 *
 * Telegram gets its bot header, light-green outgoing bubbles and — the detail
 * that matters — an inline keyboard under the bot message, which is exactly how
 * a Lemma approval arrives there. WhatsApp gets its green chrome, doodle
 * wallpaper, tailed bubbles and blue double-ticks.
 */

function TelegramPhone() {
  return (
    <div className="lp-tg2">
      <header className="lp-tg2-top">
        <i className="lp-tg2-back" />
        <span className="lp-tg2-avatar">
          <Image
            src="/landing-page/app-logos/telegram.svg"
            alt=""
            width={16}
            height={16}
          />
        </span>
        <span className="lp-tg2-who">
          <strong>
            Support Ops <em>bot</em>
          </strong>
          <small>bot · always online</small>
        </span>
        <i className="lp-tg2-menu" />
      </header>

      <div className="lp-tg2-body">
        <p className="lp-tg2-date">today</p>

        <div className="lp-tg2-in">
          <p>
            Refund request from <b>Northwind</b>. $420, above the $250
            threshold.
          </p>
          <span className="lp-tg2-time">09:12</span>
        </div>

        <div className="lp-tg2-keyboard">
          <span>✓ Approve</span>
          <span>Decline</span>
          <span className="is-wide">Open in app</span>
        </div>

        <div className="lp-tg2-out">
          <p>Approve</p>
          <span className="lp-tg2-time">
            09:13
            <i className="lp-tg2-ticks" />
          </span>
        </div>

        <div className="lp-tg2-in">
          <p>
            Done. Refund sent, <b>tkt_418</b> closed, decision logged against
            your name.
          </p>
          <span className="lp-tg2-time">09:13</span>
        </div>
      </div>

      <footer className="lp-tg2-composer">
        <i className="lp-tg2-clip" />
        <span>Message</span>
        <i className="lp-tg2-mic" />
      </footer>
    </div>
  );
}

function WhatsAppPhone() {
  return (
    <div className="lp-wa2">
      <header className="lp-wa2-top">
        <i className="lp-wa2-back" />
        <span className="lp-wa2-avatar">
          <Image
            src="/landing-page/app-logos/whatsapp.svg"
            alt=""
            width={16}
            height={16}
          />
        </span>
        <span className="lp-wa2-who">
          <strong>Field Ops</strong>
          <small>online</small>
        </span>
        <i className="lp-wa2-icon" />
        <i className="lp-wa2-icon" />
      </header>

      <div className="lp-wa2-body">
        <p className="lp-wa2-date">TODAY</p>

        <div className="lp-wa2-out">
          <p>Job 214 done. Panel replaced, customer signed off.</p>
          <span className="lp-wa2-meta">
            14:06
            <i className="lp-wa2-ticks" />
          </span>
        </div>

        <div className="lp-wa2-in">
          <p>
            Logged against <b>job_214</b>. Status set to <b>complete</b>, photo
            attached to the record.
          </p>
          <span className="lp-wa2-meta">14:06</span>
        </div>

        <div className="lp-wa2-in">
          <p>Next stop is Harlow at 15:30. Want the route?</p>
          <span className="lp-wa2-meta">14:07</span>
        </div>

        <div className="lp-wa2-out">
          <p>Yes</p>
          <span className="lp-wa2-meta">
            14:07
            <i className="lp-wa2-ticks" />
          </span>
        </div>
      </div>

      <footer className="lp-wa2-composer">
        <span>
          <i className="lp-wa2-emoji" />
          Type a message
          <i className="lp-wa2-clip" />
        </span>
        <i className="lp-wa2-send" />
      </footer>
    </div>
  );
}

export function PhoneSurface({ surface }: { surface: SurfaceMode }) {
  return surface.key === "whatsapp" ? <WhatsAppPhone /> : <TelegramPhone />;
}
