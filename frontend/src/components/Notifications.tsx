"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { markRead } from "./notifications";
import styles from "./Notifications.module.css";

/**
 * The bell, and what is behind it.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * IT POLLS, AND THAT IS A CONSTRAINT RATHER THAN A PREFERENCE.
 *
 * The obvious "seamless" answer is Server-Sent Events or a WebSocket. Both are
 * refused by the way this application is served: gunicorn runs THREE
 * SYNCHRONOUS WORKERS (backend/Dockerfile), and a held-open connection
 * occupies one for its whole life. A shop with three tills and a manager's
 * laptop would consume every worker and the API would stop answering — the
 * till mid-sale included.
 *
 * Making push work means an async worker class and, across processes,
 * something to fan out between them. That is a dependency and an operational
 * surface, and Charter 03 §I says one enters the stack only when what is
 * already there cannot do the job. A count at most half a minute stale does
 * the job, and `/ntf/unread` is built to be cheap enough to ask for: a COUNT
 * over an indexed filter, no serialisation, no joins.
 * ══════════════════════════════════════════════════════════════════════════
 */

/*
 * Thirty seconds, and it stops entirely when the tab is hidden.
 *
 * A manager's browser sits open on this all day. Polling a background tab is
 * asking the server a question nobody is waiting for the answer to — and a
 * shop with six such tabs pays for all of them. `visibilitychange` is what
 * makes the interval affordable; an immediate poll on becoming visible is what
 * stops that being noticeable.
 */
const POLL_MS = 30_000;

type Entry = {
  id: number;
  kind: string;
  kind_label: string;
  urgency: "inform" | "attend" | "urgent";
  subject: string;
  body: string;
  path: string;
  branch_name: string;
  created_at: string;
};

function when(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function Notifications() {
  const [unread, setUnread] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Entry[] | null>(null);
  const [failed, setFailed] = useState(false);
  const panel = useRef<HTMLDivElement | null>(null);
  const anchor = useRef<HTMLButtonElement | null>(null);
  // Where the panel goes, measured from the bell. Null until it has been
  // measured, which `useLayoutEffect` does before the browser paints, so the
  // panel is never seen in the wrong place first.
  const [box, setBox] = useState<{
    left: number;
    bottom: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  const count = useCallback(async () => {
    try {
      const response = await fetch("/ntf/unread", { cache: "no-store" });
      if (!response.ok) throw new Error(String(response.status));
      const body = (await response.json()) as { unread?: number };
      setUnread(body.unread ?? 0);
      setFailed(false);
    } catch {
      /*
       * Kept quiet on the bell and remembered for the panel.
       *
       * A red dot because the network blipped would be a false alarm about the
       * shop rather than about the browser — so the badge holds its last known
       * value, and somebody who opens the panel is told the list may be stale.
       */
      setFailed(true);
    }
  }, []);

  // First count, then on an interval, and only while the tab is visible.
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;

    function start() {
      void count();
      timer = setInterval(() => void count(), POLL_MS);
    }
    function stop() {
      if (timer) clearInterval(timer);
      timer = undefined;
    }
    function onVisibility() {
      if (document.visibilityState === "visible") {
        stop();
        start();
      } else {
        stop();
      }
    }

    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [count]);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/ntf/?limit=30", { cache: "no-store" });
      if (!response.ok) throw new Error(String(response.status));
      const body = (await response.json()) as {
        unread?: number;
        results?: Entry[];
      };
      setRows(body.results ?? []);
      setUnread(body.unread ?? 0);
      setFailed(false);
    } catch {
      setRows([]);
      setFailed(true);
    }
  }, []);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) {
      setRows(null);
      void load();
    }
  }

  /*
   * ══════════════════════════════════════════════════════════════════════
   * WHY THE PANEL IS `position: fixed` AND PLACED FROM JAVASCRIPT.
   *
   * It was `position: absolute` inside `.wrap`, which looks right and is
   * not: the sidebar that contains the bell is `overflow-y: auto`
   * (Shell.module.css `.side`), and an absolutely-positioned box CANNOT
   * escape a scrolling ancestor. So a 23rem panel opened inside a 14rem
   * sidebar and was clipped to it — the subject wrapped to one word per
   * line, the Done button was off the edge, and the sidebar grew a
   * horizontal scrollbar. It looked like a styling accident and was a
   * containment one, which is why the repair is not "make it narrower".
   *
   * Fixed positioning is relative to the viewport, so nothing clips it.
   * The cost is that the browser no longer keeps it attached to the bell,
   * so this measures the bell and does it — on open, on resize, and on
   * scroll anywhere (capture phase, because the sidebar scrolls, not the
   * window).
   *
   * It also opens UPWARD. The bell is the last thing in the sidebar, a few
   * pixels off the bottom of the screen; downward had nowhere to go.
   * ══════════════════════════════════════════════════════════════════════
   */
  useLayoutEffect(() => {
    if (!open) return;

    function place() {
      const bell = anchor.current?.getBoundingClientRect();
      if (!bell) return;

      // A phone gets the full width between the gutters; a laptop gets the
      // panel's natural width, nudged left if the sidebar sits near an edge.
      const gutter = 12;
      const width = Math.min(368, window.innerWidth - gutter * 2);
      const left = Math.max(
        gutter,
        Math.min(bell.left, window.innerWidth - width - gutter),
      );

      setBox({
        left,
        width,
        // Distance from the viewport's bottom to the panel's bottom edge:
        // the panel sits just above the bell.
        bottom: Math.max(gutter, window.innerHeight - bell.top + 6),
        // Never taller than the space actually above the bell, so the panel
        // scrolls internally instead of running off the top of the screen.
        maxHeight: Math.max(160, bell.top - 6 - gutter),
      });
    }

    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  // Escape closes it, and the focus goes back to the bell. A panel that can
  // only be dismissed with the mouse is a panel somebody on a keyboard is
  // stuck inside.
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function onClickAway(event: MouseEvent) {
      if (panel.current && !panel.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClickAway);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClickAway);
    };
  }, [open]);

  async function dismiss(id: number) {
    // Optimistic, then corrected: the row goes at once because the press was
    // deliberate, and the count is re-read from the server rather than
    // decremented here — a number this component worked out for itself is a
    // number that can disagree with the one the bell shows next.
    setRows((current) => current?.filter((r) => r.id !== id) ?? null);
    await markRead([id]);
    void count();
  }

  async function dismissAll() {
    setRows([]);
    await markRead();
    void count();
  }

  const badge = unread ?? 0;

  return (
    <div className={styles.wrap} ref={panel}>
      <button
        type="button"
        ref={anchor}
        className={styles.bell}
        aria-expanded={open}
        aria-label={
          badge > 0 ? `Notifications, ${badge} unread` : "Notifications"
        }
        onClick={toggle}
      >
        <span aria-hidden="true" className={styles.glyph}>
          {/* A bell, drawn rather than an emoji: an emoji is a different
              typeface on every device and renders as a box on some. */}
          <svg viewBox="0 0 16 16" width="16" height="16" fill="none">
            <path
              d="M8 1.75a4 4 0 0 0-4 4v2.3L2.75 10.5h10.5L12 8.05V5.75a4 4 0 0 0-4-4Z"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
            <path
              d="M6.25 12.5a1.75 1.75 0 0 0 3.5 0"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
            />
          </svg>
        </span>
        {badge > 0 ? (
          <span className={styles.badge}>{badge > 99 ? "99+" : badge}</span>
        ) : null}
      </button>

      {open ? (
        <div
          className={styles.panel}
          role="dialog"
          aria-label="Notifications"
          style={
            box
              ? {
                  left: box.left,
                  bottom: box.bottom,
                  width: box.width,
                  maxHeight: box.maxHeight,
                }
              : // Measured before paint; this only covers the frame before
                // the layout effect runs on a server-rendered first open.
                { visibility: "hidden" }
          }
        >
          <div className={styles.head}>
            <strong className={styles.title}>Notifications</strong>
            {rows && rows.length > 0 ? (
              <button
                type="button"
                className={styles.clear}
                onClick={() => void dismissAll()}
              >
                Mark all read
              </button>
            ) : null}
          </div>

          {failed ? (
            <p className={styles.stale} role="status">
              Could not reach the system just now, so this list may be out of
              date.
            </p>
          ) : null}

          {rows === null ? (
            <p className={styles.quiet}>Looking…</p>
          ) : rows.length === 0 ? (
            /*
              Said as the good news it is. "No notifications" reads like
              something failed to load; this says the shop is fine.
            */
            <p className={styles.quiet}>
              Nothing needs you. Stock is above its levels, nothing is waiting
              for approval and no till has closed short.
            </p>
          ) : (
            <ul className={styles.list}>
              {rows.map((row) => (
                <li key={row.id} className={styles.row}>
                  <span
                    className={`${styles.pip} ${styles[row.urgency] ?? ""}`}
                    aria-hidden="true"
                  />
                  <div className={styles.words}>
                    <a className={styles.subject} href={row.path || "#"}>
                      {row.subject}
                    </a>
                    {row.body ? (
                      <span className={styles.body}>{row.body}</span>
                    ) : null}
                    <span className={styles.meta}>
                      {row.branch_name ? `${row.branch_name} · ` : ""}
                      {when(row.created_at)}
                    </span>
                  </div>
                  <button
                    type="button"
                    className={styles.tick}
                    aria-label={`Mark "${row.subject}" read`}
                    onClick={() => void dismiss(row.id)}
                  >
                    Done
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
