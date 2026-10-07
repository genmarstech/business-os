/**
 * The Genmars Business Platform mark.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE GENMARS G IS NOT TOUCHED. NOT ITS GEOMETRY, NOT BY A UNIT.
 *
 * `Mark.tsx` carries a warning that the mark exists in five places —
 * gen-website, internals-tm, gen-portal, business-os and the promo film —
 * plus two .svg files and the rasters generated from them, and that it
 * existing in two shapes is worse than either shape.
 *
 * So this is a SIBLING component, not an edit. Same centre (60,60), same
 * radius 34, same 9-unit stroke, same crossbar, same orbit ellipse at -30°.
 * Copy the path data out of Mark.tsx and compare it: it is identical. What
 * differs is the palette and what sits beside it.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── GREEN IS THE PRODUCT SIGNAL ────────────────────────────────────────────
 *
 * Genmars is Imperial Topaz on Ignition; the Business Platform is green, the
 * same green the application is already built in. A sub-brand that shares a
 * silhouette and changes hue reads as a member of a family, which is what it
 * is — the shape says Genmars, the colour says which Genmars.
 *
 * The two greens are chosen to sit at the SAME LUMINANCE as the two originals
 * rather than to look nice on a white page. #2E7D57 lands where #8B5A48 does
 * and #5FB98A where #DB7B51 does, so the mark holds up on the light canvas
 * and on the dark one without inverting — and a logo that inverts is a
 * different logo.
 *
 * ── WHY THE LETTERS ARE NOT IN THE SQUARE MARK ─────────────────────────────
 *
 * This component is rendered at 22, 26, 28 and 30 pixels — every single place
 * it is used today. A letter drawn inside the 120-unit box at those sizes is
 * five or six pixels tall. Two of them, side by side, inside the aperture of
 * a G, are not letters; they are a smudge that makes the mark look damaged.
 *
 * That is the exact failure `Mark.tsx` documents at length: the G's counters
 * were sealed to 0.92px and the mark read as a C with a nub at every size
 * anybody saw. The lesson there was to measure the aperture rather than trust
 * the drawing, and it applies here before the drawing exists.
 *
 * So the B and the P live in the LOCKUP, where they are given the full cap
 * height of the box and are legible at the same 26 pixels. The square form is
 * the mark; the lockup is the mark plus its initials. Both are this product's
 * and neither is a compromise of the other.
 */

/*
 * ── THE APP ICON IS NOT THESE TWO GREENS, AND THAT IS NOT A DRIFT ──────────
 *
 * src/app/icon.source.svg puts a CREAM G on a deep green ground, because an
 * icon brings its own surface and this component does not. #2E7D57 is chosen
 * to hold on the application's near-white and near-black canvases; on the
 * family's plum it measures 2.77:1 and disappears. The icon's own banner has
 * the figures. Same geometry, same family, one of them has a ground.
 */

/** Imperial Topaz's place in the family, in green. */
const LETTER = "#2E7D57";
/** Ignition's place, in green: lighter, more saturated, the moving part. */
const ORBIT = "#5FB98A";

/** The G, exactly as `Mark.tsx` draws it. Recoloured, never reshaped. */
function TheG() {
  return (
    <>
      <path
        d="M85.27 37.25 A34 34 0 1 0 85.27 82.75"
        fill="none"
        stroke={LETTER}
        strokeWidth="9"
        strokeLinecap="round"
      />
      <path
        d="M74 60 H92.5"
        fill="none"
        stroke={LETTER}
        strokeWidth="9"
        strokeLinecap="round"
      />
      <ellipse
        cx="60"
        cy="60"
        rx="55"
        ry="17"
        fill="none"
        stroke={ORBIT}
        strokeWidth="4"
        transform="rotate(-30 60 60)"
      />
    </>
  );
}

/**
 * B and P, drawn in the G's own language.
 *
 * Stroke-built rather than filled, at the same 9 units with the same round
 * caps, so they read as having been made by the same tool. Their bowls are
 * semicircles — the G is a circle, and a bowl that is an arc of one belongs
 * beside it in a way that a typeface's optically-corrected curve does not.
 *
 * Cap height 26 → 94 is the G's own diameter, 68 units, so the three
 * characters share a baseline and a cap line exactly.
 */
function TheInitials() {
  return (
    <>
      {/* B — spine, then two stacked bowls.
          ── THE BOWLS ARE ELLIPTICAL, AND HAD TO BECOME SO ──────────────
          Drawn first as semicircles, which is what a 34-unit cap height
          forces: radius 17, projecting 17. Against a 9-unit stroke that
          leaves an 8-unit counter, and at every size this is used the two
          counters filled in and the letter read as a flag on a pole — the
          same sealed-aperture failure Mark.tsx documents for the G, made
          again one component later.
          21 wide over 17 tall opens the counters to 12 and the B reads as
          a B. Measured, not guessed. */}
      <path
        d="M134 26 V94"
        fill="none"
        stroke={LETTER}
        strokeWidth="9"
        strokeLinecap="round"
      />
      <path
        d="M134 26 A21 17 0 0 1 134 60 A21 17 0 0 1 134 94"
        fill="none"
        stroke={LETTER}
        strokeWidth="9"
        strokeLinecap="round"
      />

      {/* P — the same spine, one bowl, larger than the B's so it does not
          read as a B with its lower half rubbed out. */}
      <path
        d="M178 26 V94"
        fill="none"
        stroke={LETTER}
        strokeWidth="9"
        strokeLinecap="round"
      />
      <path
        d="M178 26 A23 19 0 0 1 178 64"
        fill="none"
        stroke={LETTER}
        strokeWidth="9"
        strokeLinecap="round"
      />
    </>
  );
}

/**
 * `lockup` adds the initials and widens the box. Height is what `size`
 * controls in both forms, so a lockup and a square mark set to the same size
 * sit on the same line without either being nudged.
 *
 * Decorative wherever a wordmark sits beside it — the text already says what
 * this is, and a screen reader announcing it twice is noise.
 */
export function BusinessMark({
  size = 28,
  lockup = false,
}: {
  size?: number;
  lockup?: boolean;
}) {
  // 211 leaves 4.5 units of round cap past the P's bowl at x=201, and no more:
  // trailing space inside an svg is space the layout cannot see to remove.
  const width = lockup ? 211 : 120;

  return (
    <svg
      viewBox={`0 0 ${width} 120`}
      width={(size * width) / 120}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <TheG />
      {lockup ? <TheInitials /> : null}
    </svg>
  );
}
