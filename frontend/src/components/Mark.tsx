/**
 * ══════════════════════════════════════════════════════════════════════════
 * THE G'S ARC WAS OPENED FROM ±25.1° TO ±42° ON 2026-09-26, AND THE OLD
 * GEOMETRY WAS MEASURABLY BROKEN.
 *
 * The arc used to run from ±25.1° with a 9-unit round cap on each end.
 * Measure what that leaves between the cap and the crossbar:
 *
 *     upper arc end, incl. round cap   reaches y = 54.6
 *     crossbar top edge                sits at  y = 55.5
 *     clear counter                    0.92px   — in a 120-unit box
 *
 * Both of the G's counters were sealed to under a pixel, so at every size
 * anyone actually saw — favicon, header, loading mark, the og image — the
 * right-hand side filled in solid and the mark read as a C with a nub. It
 * was not a rendering artefact and no amount of lighting fixed it: there
 * was no aperture to light.
 *
 * At ±42° the counter opens to 9.25px and the crossbar reads as a crossbar.
 * Nothing else moved: same centre (60,60), same radius 34, same stroke
 * weight, same crossbar, same silhouette, same ring. The arc is 17° shorter
 * at each end and that is the entire change.
 *
 * ⚠ IT IS CHANGED IN ALL FIVE PLACES AT ONCE — gen-website, internals-tm,
 * gen-portal, business-os and the promo film — plus both .svg files and the
 * raster icons regenerated from them. The mark existing in two shapes is
 * worse than either shape. If this is ever revised again, revise it
 * everywhere in the same change.
 *
 * The old path, for the record:  M90.8 45.6 A34 34 0 1 0 90.8 74.4
 * ══════════════════════════════════════════════════════════════════════════
 */
/**
 * The Genmars mark.
 *
 * Inline rather than an <img> so it inherits nothing and changes nothing: the
 * two stroke colours are brand constants (Imperial Topaz for the G, Ignition
 * for the orbit) and they are the same in both themes, because a logo that
 * inverts is a different logo.
 *
 * Decorative wherever a wordmark sits beside it — the text already says
 * "Genmars", and a screen reader announcing it twice is noise.
 */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 120 120"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M85.27 37.25 A34 34 0 1 0 85.27 82.75"
        fill="none"
        stroke="#8B5A48"
        strokeWidth="9"
        strokeLinecap="round"
      />
      <path
        d="M74 60 H92.5"
        fill="none"
        stroke="#8B5A48"
        strokeWidth="9"
        strokeLinecap="round"
      />
      <ellipse
        cx="60"
        cy="60"
        rx="55"
        ry="17"
        fill="none"
        stroke="#DB7B51"
        strokeWidth="4"
        transform="rotate(-30 60 60)"
      />
    </svg>
  );
}
