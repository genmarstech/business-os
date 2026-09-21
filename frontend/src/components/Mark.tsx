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
        d="M90.8 45.6 A34 34 0 1 0 90.8 74.4"
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
