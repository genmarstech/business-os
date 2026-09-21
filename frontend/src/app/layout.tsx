import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Jost } from "next/font/google";

import "./globals.css";

/**
 * Three faces, each doing a job the others cannot. Self-hosted at build time
 * by next/font — no request to Google at runtime, which is both faster and one
 * fewer third party seeing who uses a shop's till.
 *
 * Jost is the Genmars face and carries the identity. Its geometric figures are
 * handsome and slightly ambiguous, which is fine in a heading and not fine in
 * a column of money — so Plex Sans (genuinely distinct 1 l I, 0 O) takes
 * everything a person reads to make a decision, and Plex Mono takes every
 * amount, SKU, barcode and receipt preview.
 */
const jost = Jost({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-jost",
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
      className={`${jost.variable} ${plexSans.variable} ${plexMono.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
