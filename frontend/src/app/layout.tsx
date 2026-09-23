import type { Metadata } from "next";
import {
  IBM_Plex_Mono,
  IBM_Plex_Sans,
  Jost,
  Source_Serif_4,
} from "next/font/google";

import "./globals.css";

/**
 * Three faces, each doing a job the others cannot. Self-hosted at build time
 * by next/font — no request to Google at runtime, which is both faster and one
 * fewer third party seeing who uses a shop's till.
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
const jost = Jost({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-jost",
  display: "swap",
});

/*
 * Weights 400 and 600 only. Source Serif is a text face as well as a display
 * one, so it is tempting to reach for it everywhere; two weights is the fence
 * that keeps it in headings.
 */
const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  weight: ["400", "600"],
  variable: "--font-source-serif",
  display: "swap",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
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
