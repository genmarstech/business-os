"use client";

/**
 * The browser's own barcode decoder.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * NO DEPENDENCY, BY CHARTER 03 §I, AND THE COST IS NAMED RATHER THAN HIDDEN.
 *
 * `BarcodeDetector` is built into Chromium, so scanning with a phone camera
 * costs this bundle nothing. The alternative is a WebAssembly decoder of
 * several hundred kilobytes in a till that exists to keep up with a queue,
 * and §I is explicit: a dependency enters the stack only when what is already
 * there cannot do the job.
 *
 * ⚠ WHAT THAT COSTS: SAFARI AND EVERY BROWSER ON iOS CANNOT DO THIS.
 *
 *   Every iOS browser is WebKit underneath, Chrome for iOS included, so this
 *   is not "iPhone users should install Chrome" — it is iPhones, full stop.
 *   Android Chrome is the overwhelming majority of phones in this market and
 *   that is why the trade is the right way round today.
 *
 *   It must therefore NEVER be a silent failure. `scanning()` is asked before
 *   a Scan button is drawn, and where the answer is no the screen says so and
 *   points at the two paths that still work: type the code, or use a wedge
 *   scanner, which is a keyboard and works everywhere.
 *
 *   If iPhones start mattering, the fix is a lazily-imported wasm decoder
 *   behind this same function, and nothing above it changes. That is the
 *   whole reason the capability question lives here rather than inline in a
 *   component.
 * ══════════════════════════════════════════════════════════════════════════
 */

/*
 * ── THE TYPES, BECAUSE TypeScript DOES NOT SHIP THEM ───────────────────────
 * Declared minimally and locally rather than pulled from a @types package: a
 * dependency for three interfaces is the thing §I is about, and a wrong
 * guess here is a compile error rather than a runtime surprise.
 */
type DetectedBarcode = {
  rawValue: string;
  format: string;
};

type BarcodeDetectorLike = {
  detect(source: CanvasImageSource): Promise<DetectedBarcode[]>;
};

type BarcodeDetectorConstructor = {
  new (options?: { formats?: string[] }): BarcodeDetectorLike;
  getSupportedFormats(): Promise<string[]>;
};

function constructor(): BarcodeDetectorConstructor | null {
  if (typeof window === "undefined") return null;
  const found = (window as unknown as Record<string, unknown>).BarcodeDetector;
  return typeof found === "function"
    ? (found as BarcodeDetectorConstructor)
    : null;
}

/**
 * ── WHAT A SHOP ACTUALLY SCANS, IN THE ORDER IT MATTERS ────────────────────
 *
 * The retail symbologies first: EAN-13 is what is printed on almost
 * everything sold in Kenya, EAN-8 on small packages, and the two UPC variants
 * on anything imported from America. Then the two code families a shop prints
 * for ITSELF, which is how loose goods and own-packed items get a label at
 * all. QR last, because a shop that puts QR on a shelf edge has chosen to.
 *
 * ⚠ ASKED FOR, NOT ASSUMED. Constructing a detector with a format the device
 *   does not implement throws, and the set genuinely varies by Android
 *   version — so this is intersected with `getSupportedFormats()` and the
 *   rest are dropped. Requesting fewer formats is also faster per frame,
 *   which is the difference between a scan that feels instant and one
 *   somebody gives up on.
 */
const WANTED = [
  "ean_13",
  "ean_8",
  "upc_a",
  "upc_e",
  "code_128",
  "code_39",
  "itf",
  "qr_code",
];

/** Can this browser scan at all? Asked before a Scan button is offered. */
export function scanning(): boolean {
  return (
    constructor() !== null &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    /*
     * ⚠ A SECURE CONTEXT IS REQUIRED AND ITS ABSENCE LOOKS LIKE A BROKEN
     *   CAMERA. getUserMedia is refused over plain http, so a till reached by
     *   IP address on a shop's LAN would offer a Scan button that always
     *   fails. Asked here so the button is simply absent instead.
     */
    window.isSecureContext
  );
}

/** A detector for the formats this device actually implements, or null. */
export async function detector(): Promise<BarcodeDetectorLike | null> {
  const Detector = constructor();
  if (!Detector) return null;

  try {
    const supported = await Detector.getSupportedFormats();
    const formats = WANTED.filter((f) => supported.includes(f));
    // Nothing in common is not a device to run a scanner on, and an empty
    // `formats` array means "every format" rather than "none" — which would
    // be slow and would silently contradict the list above.
    if (formats.length === 0) return null;
    return new Detector({ formats });
  } catch {
    return null;
  }
}
