"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { detector } from "./barcode";
import styles from "./scanner.module.css";

/**
 * The camera, pointed at a barcode.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * ONE COMPONENT FOR SELLING AND FOR COUNTING, BECAUSE IT IS ONE MECHANISM.
 *
 * A till scans to add a line; a stock take scans to find the shelf row it is
 * standing in front of. The difference is what the caller does with the
 * digits, which is the one thing passed in. Two copies of camera handling
 * would be two places to get the teardown wrong, and the teardown is the part
 * that matters — see below.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ⚠ THE CAMERA IS STOPPED ON EVERY PATH OUT, AND THAT IS NOT A TIDINESS
 *   CONCERN. A MediaStream whose tracks are not stopped keeps the sensor
 *   powered, the phone's recording indicator lit, and the battery draining —
 *   on a device somebody is holding for a whole shift in an aisle. It also
 *   looks exactly like a shop's own software spying on its staff, which is a
 *   thing nobody will ask us about before deciding.
 *
 *   So the stream lives in a ref, every exit goes through `stop()`, and the
 *   effect's cleanup calls it too — covering unmount, navigation and the
 *   component being torn down by an error above it.
 */

/**
 * How long the same code is ignored after it is accepted.
 *
 * The detector reads the same label on every frame, so without this one
 * scanned item becomes thirty of it in the basket before the phone is moved.
 * Long enough to lift the phone off a label and onto the next one; short
 * enough that scanning two identical items in a row genuinely registers
 * twice, which a shop does constantly.
 */
const SAME_CODE_PAUSE_MS = 1500;

/** Frames per second to decode at. */
const SCAN_INTERVAL_MS = 180;

export function Scanner({
  title,
  hint,
  continuous,
  onScan,
  onClose,
}: {
  title: string;
  hint: string;
  /**
   * Keep the camera open after a hit.
   *
   * True at a till, where somebody scans a basket of six things and closing
   * between each would make scanning slower than typing. False on a stock
   * take, where a hit means "this is the row I want" and the next thing to
   * happen is typing a number into it.
   */
  continuous: boolean;
  /** Return false to reject a code — an unknown barcode should not beep. */
  onScan: (code: string) => boolean;
  onClose: () => void;
}) {
  const video = useRef<HTMLVideoElement | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const seen = useRef<Map<string, number>>(new Map());
  const live = useRef(true);

  /*
   * ── onScan LIVES IN A REF, AND THE REASON IS A REAL BUG ──────────────────
   *
   * The callers rebuild it on every render — the till's closes over the
   * catalogue and the tax rules, the stock take's over what has been counted
   * so far. Two wrong ways to use that:
   *
   *   · as an effect dependency — the camera is torn down and permission
   *     re-requested on every keystroke behind this screen.
   *   · captured in the effect's closure — the scan loop keeps calling the
   *     FIRST version for ever, so a code scanned after the catalogue
   *     refreshed is matched against the catalogue as it was when the camera
   *     opened, and a product added minutes ago reads as unknown.
   *
   * So the effect depends on neither and reads through here, which is updated
   * on every render below.
   *
   * `onClose` is here for the same reason and it is the sharper case: both
   * callers pass an inline arrow, so it is a new function on every render of
   * the PARENT — and the till's parent re-renders every time the basket
   * changes. Depending on it would have restarted the camera after every
   * single scanned item, which is the one place this component is supposed to
   * stay out of the way.
   */
  const scan = useRef(onScan);
  scan.current = onScan;

  const leave = useRef(onClose);
  leave.current = onClose;

  const [error, setError] = useState("");
  const [hit, setHit] = useState("");
  const [miss, setMiss] = useState("");
  const [torch, setTorch] = useState<boolean | null>(null);

  const stop = useCallback(() => {
    live.current = false;
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }, []);

  // Stable: `stop` never changes and the caller's handler is read through a
  // ref, so this is created once and the camera effect below is never
  // re-armed by a parent's re-render.
  const close = useCallback(() => {
    stop();
    leave.current();
  }, [stop]);

  useEffect(() => {
    live.current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function run() {
      const reader = await detector();
      if (!reader) {
        setError("This device cannot scan. Type the code instead.");
        return;
      }

      let media: MediaStream;
      try {
        media = await navigator.mediaDevices.getUserMedia({
          // The back camera. Without this a phone opens the selfie camera,
          // which is pointed at the person rather than the shelf.
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
      } catch (caught) {
        /*
         * Told apart because the fixes are different and only one of them is
         * something the person holding the phone can do. "Permission denied"
         * is a browser setting they can change; anything else is a camera
         * that is broken, absent or already in use by another app.
         */
        const name = (caught as { name?: string })?.name ?? "";
        setError(
          name === "NotAllowedError" || name === "SecurityError"
            ? "The camera was not allowed. Turn it on for this site in your browser settings, or type the code instead."
            : name === "NotFoundError"
              ? "No camera on this device. Type the code instead."
              : "The camera would not start. It may be in use by another app.",
        );
        return;
      }

      // Torn down while the permission prompt was open. Stopping the tracks
      // we were just granted is the whole point — otherwise the camera stays
      // on after the screen has gone.
      if (!live.current) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }

      stream.current = media;
      if (video.current) {
        video.current.srcObject = media;
        try {
          await video.current.play();
        } catch {
          // Autoplay refused. The stream is live and the frames are still
          // readable, so this is not worth a message.
        }
      }

      // The lamp, where the device has one. A shelf at the back of a shop is
      // darker than the phone's auto-exposure can rescue, and an unreadable
      // barcode in an aisle is the whole feature failing quietly.
      const track = media.getVideoTracks()[0];
      const capabilities = track?.getCapabilities?.() as
        | { torch?: boolean }
        | undefined;
      if (capabilities?.torch) setTorch(false);

      async function tick() {
        if (!live.current) return;
        const element = video.current;
        if (element && element.readyState >= 2) {
          try {
            const found = await reader!.detect(element);
            const now = Date.now();
            for (const code of found) {
              const value = code.rawValue?.trim();
              if (!value) continue;

              const last = seen.current.get(value) ?? 0;
              if (now - last < SAME_CODE_PAUSE_MS) continue;
              seen.current.set(value, now);

              const taken = scan.current(value);
              if (taken) {
                setMiss("");
                setHit(value);
                // A short buzz, where the device has a motor. Somebody
                // scanning a shelf is looking at the shelf, not the screen.
                navigator.vibrate?.(60);
                if (!continuous) {
                  close();
                  return;
                }
              } else {
                /*
                 * A code nothing matched. Said rather than ignored: a silent
                 * non-response is indistinguishable from a camera that did
                 * not read the label, so somebody rescans the same thing
                 * repeatedly instead of learning the product has no barcode
                 * on file.
                 */
                setHit("");
                setMiss(value);
              }
            }
          } catch {
            // One unreadable frame is the normal case, not a fault.
          }
        }
        timer = setTimeout(() => void tick(), SCAN_INTERVAL_MS);
      }

      void tick();
    }

    void run();

    return () => {
      live.current = false;
      if (timer) clearTimeout(timer);
      stop();
    };
  }, [continuous, stop, close]);

  async function toggleTorch() {
    const track = stream.current?.getVideoTracks()[0];
    if (!track) return;
    const next = !torch;
    try {
      /*
       * Cast through `unknown` because `torch` is not in TypeScript's DOM
       * typings — it is in the MediaStream Image Capture spec, which Chromium
       * implements and the lib.dom.d.ts bundled with TypeScript does not yet
       * describe. Guarded by `getCapabilities().torch` above, so this never
       * runs on a device that would reject it; the try/catch covers the
       * devices that advertise it and refuse it anyway.
       */
      await track.applyConstraints({
        advanced: [{ torch: next }],
      } as unknown as MediaTrackConstraints);
      setTorch(next);
    } catch {
      // Some devices advertise the capability and refuse the constraint.
      // Hiding the control after the fact is better than a dead button.
      setTorch(null);
    }
  }

  return (
    <div className={styles.sheet} role="dialog" aria-modal="true" aria-label={title}>
      <div className={styles.frame}>
        <video
          ref={video}
          className={styles.video}
          playsInline
          muted
          // Hidden from the accessibility tree: a live camera preview has
          // nothing to announce, and the status line below says what matters.
          aria-hidden="true"
        />
        <div className={styles.reticle} aria-hidden="true" />
      </div>

      <div className={styles.bar}>
        <div className={styles.words}>
          <strong className={styles.title}>{title}</strong>
          {error ? (
            <span className={styles.error} role="alert">
              {error}
            </span>
          ) : miss ? (
            <span className={styles.miss} role="status">
              Nothing here has the barcode {miss}. Add it to the product, or
              find it by name.
            </span>
          ) : hit ? (
            <span className={styles.hit} role="status">
              Scanned {hit}
            </span>
          ) : (
            <span className={styles.hint}>{hint}</span>
          )}
        </div>

        <div className={styles.buttons}>
          {torch !== null ? (
            <button
              type="button"
              className={styles.light}
              aria-pressed={torch}
              onClick={() => void toggleTorch()}
            >
              {torch ? "Light off" : "Light on"}
            </button>
          ) : null}
          <button type="button" className={styles.done} onClick={close}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
