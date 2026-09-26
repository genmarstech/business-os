import type { Metadata } from "next";
import localFont from "next/font/local";

import "./globals.css";

/**
 * Four faces, each doing a job the others cannot. Served from this origin —
 * no request to Google at runtime, which is faster and one fewer third party
 * seeing who uses a shop's till.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * next/font/LOCAL, NOT next/font/google, AND THAT IS THE WHOLE POINT.
 *
 * `next/font/google` also self-hosts — but it DOWNLOADS the files during the
 * build, which put fonts.googleapis.com on the critical path of
 * `docker build`. A transient failure there fails the image with "An error
 * occurred in `next/font`" while nothing about this application is wrong.
 *
 * It failed CI on a commit that changed one SVG path, and then failed the
 * deploy on the server minutes later. A deploy somebody else's CDN can break
 * is not a deploy we control, so the files live in src/app/fonts/ and a
 * build needs no network beyond the npm registry.
 *
 * To add a face or a weight: edit WANTED in scripts/vendor-fonts.mjs, run
 * it, and add the declaration here. See src/app/fonts/README.md.
 *
 * ── THREE OF THESE ARE ONE FILE COVERING A RANGE ──────────────────────────
 *
 * Jost, Source Serif and Plex Sans are variable fonts: a single woff2 serves
 * every weight, so they declare `weight: "400 600"` rather than one entry
 * each. Plex Mono is static on Google Fonts and genuinely needs a file per
 * weight — which is why it alone has two.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── THREE FACES, AND EACH HAS ONE JOB ─────────────────────────────────────
 *
 * Source Serif carries the product's own voice: headings, and the one big
 * line on the front door. It is what makes this look like a tool rather than
 * like a page about the company.
 *
 * Jost is the GENMARS face and now appears only where Genmars speaks — the
 * mark in the corner. Running the whole product in it made the company and
 * the product indistinguishable, which served neither.
 *
 * Plex Sans takes everything a person reads to make a decision, because its
 * 1 l I and 0 O are genuinely distinct and a serif's are not at 12px. Plex
 * Mono takes every amount, SKU, barcode and receipt preview.
 */
const jost = localFont({
  src: "./fonts/jost-variable.woff2",
  weight: "400 600",
  style: "normal",
  variable: "--font-jost",
  display: "swap",
});

/*
 * Weights 400 and 600 only. Source Serif is a text face as well as a display
 * one, so it is tempting to reach for it everywhere; two weights is the fence
 * that keeps it in headings.
 */
const sourceSerif = localFont({
  src: "./fonts/source-serif-4-variable.woff2",
  weight: "400 600",
  style: "normal",
  variable: "--font-source-serif",
  display: "swap",
});

const plexSans = localFont({
  src: "./fonts/ibm-plex-sans-variable.woff2",
  weight: "400 600",
  style: "normal",
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = localFont({
  // Static, so one file per weight — see the banner.
  src: [
    { path: "./fonts/ibm-plex-mono-400.woff2", weight: "400", style: "normal" },
    { path: "./fonts/ibm-plex-mono-500.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Genmars Business Platform",
    template: "%s · Business Platform",
  },
  description:
    "Branches, catalogue, stock and tills, for a business running more than one of any of them.",
  /*
   * Not indexable, and this is not merely a default.
   *
   * Every page behind the door is one business's trading data. Caddy also
   * sends X-Robots-Tag: noindex for the whole host (deploy/business.caddy);
   * this is the second half, because a header and a tag are read by different
   * things and the cost of both is nothing.
   */
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${sourceSerif.variable} ${jost.variable} ${plexSans.variable} ${plexMono.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
