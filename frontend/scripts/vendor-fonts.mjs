/**
 * Download the webfonts this app uses into src/app/fonts/.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHY THE FONTS ARE IN THE REPOSITORY RATHER THAN FETCHED BY next/font/google.
 *
 * `next/font/google` self-hosts the files, but it downloads them DURING THE
 * BUILD. That put fonts.googleapis.com on the critical path of `docker build`
 * — so a transient failure at Google fails the image build with "An error
 * occurred in `next/font`" while nothing about the application is wrong.
 *
 * That is not hypothetical. On 2026-09-26 it failed CI on a commit that
 * changed one SVG path, then failed the deploy on the server minutes later.
 * A deploy somebody else's CDN can break is not a deploy we control.
 *
 * Run this only to add a face or a weight. Nothing in the build calls it.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 *     node scripts/vendor-fonts.mjs
 *
 * ── VARIABLE FAMILIES ARE ONE FILE, NOT ONE PER WEIGHT ─────────────────────
 *
 * Google's css2 API emits a separate @font-face per requested weight even
 * when the family is variable — and for a variable family all of those
 * blocks point at the SAME woff2. Saving one copy per weight writes three
 * identical 26 KB files and makes the browser fetch the same bytes three
 * times under three hashed names, because nothing downstream knows they are
 * the same font.
 *
 * So downloads are deduplicated by URL. A family whose weights collapse to
 * one file is variable and is written once as `<family>-variable.woff2`;
 * layout.tsx then declares it with a weight RANGE. A family with distinct
 * URLs is static and keeps one file per weight.
 *
 * LICENSING. Every family here is under the SIL Open Font License, which
 * permits redistribution including bundled in an application, and requires
 * the licence travel with the files. The `*-OFL.txt` files are the upstream
 * texts, unmodified, and must not be deleted.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

/** Exactly what src/app/layout.tsx asks for. Keep the two in step. */
const WANTED = [
  { family: "Jost", weights: [400, 500, 600] },
  { family: "Source Serif 4", weights: [400, 600] },
  { family: "IBM Plex Sans", weights: [400, 500, 600] },
  { family: "IBM Plex Mono", weights: [400, 500] },
];

// Google serves woff2 only to a UA it believes supports it. Ask as a current
// Chrome or the CSS comes back full of truetype URLs.
const UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";

const OUT = path.resolve("src/app/fonts");
mkdirSync(OUT, { recursive: true });

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const get = async (url, as) => {
  const r = await fetch(url, { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return as === "buf" ? Buffer.from(await r.arrayBuffer()) : r.text();
};

const manifest = [];

for (const { family, weights } of WANTED) {
  const css = await get(
    `https://fonts.googleapis.com/css2?family=${encodeURIComponent(family)}` +
      `:wght@${weights.join(";")}&display=swap`,
  );

  /*
   * One @font-face per subset per weight — latin, latin-ext, cyrillic and so
   * on. layout.tsx asks for `latin` only, so take the latin blocks and ignore
   * the rest; taking every block would multiply the bytes for glyphs nothing
   * renders.
   */
  const latin = css
    .split("/* ")
    .slice(1)
    .filter((b) => b.startsWith("latin */"));

  const byWeight = new Map();
  for (const weight of weights) {
    const block = latin.find((b) =>
      new RegExp(`font-weight:\\s*${weight}\\b`).test(b),
    );
    if (!block) throw new Error(`${family} ${weight}: no latin block`);
    const url = block.match(/url\((https:\/\/[^)]+\.woff2)\)/)?.[1];
    if (!url) throw new Error(`${family} ${weight}: no woff2 url`);
    byWeight.set(weight, url);
  }

  const distinct = new Set(byWeight.values());
  const variable = distinct.size === 1 && weights.length > 1;

  if (variable) {
    const url = [...distinct][0];
    const name = `${slug(family)}-variable.woff2`;
    const bytes = await get(url, "buf");
    writeFileSync(path.join(OUT, name), bytes);
    const range = `${Math.min(...weights)} ${Math.max(...weights)}`;
    manifest.push({ family, file: name, weight: range, variable: true });
    console.log(
      `saved ${name}  ${(bytes.length / 1024).toFixed(1)} KB  ` +
        `(variable, covers ${weights.join("/")})`,
    );
  } else {
    for (const [weight, url] of byWeight) {
      const name = `${slug(family)}-${weight}.woff2`;
      const bytes = await get(url, "buf");
      writeFileSync(path.join(OUT, name), bytes);
      manifest.push({ family, file: name, weight: String(weight), variable: false });
      console.log(`saved ${name}  ${(bytes.length / 1024).toFixed(1)} KB  (static)`);
    }
  }
}

console.log("\nDeclare these in src/app/layout.tsx:");
for (const m of manifest) {
  console.log(`  ${m.file.padEnd(32)} weight: "${m.weight}"`);
}
