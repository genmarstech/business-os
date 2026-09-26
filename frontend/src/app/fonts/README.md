# Vendored webfonts

Five `.woff2` files, ~149 KB, latin subset only.

Three of the four families are **variable** — one file covers the whole
weight range, so they are stored once as `<family>-variable.woff2` and
declared in `layout.tsx` with `weight: "400 600"`. IBM Plex Mono is static
on Google Fonts and is the only one with a file per weight.

Saving one copy per weight for a variable family writes three byte-identical
files and makes the browser fetch the same font three times under three
hashed names. `scripts/vendor-fonts.mjs` deduplicates by URL to prevent it.

## Why these are in git

`next/font/google` self-hosts the files, but it **downloads them during the
build** — which put `fonts.googleapis.com` on the critical path of
`docker build`. A transient failure at Google then fails the image build with
`An error occurred in next/font` and nothing about the application is wrong.

That is not hypothetical. On 2026-09-26 it failed CI on a commit that changed
one SVG path, and then failed the deploy on the server minutes later. A
deploy that somebody else's CDN can break is not a deploy we control.

So the files are here, `layout.tsx` uses `next/font/local`, and a build needs
no network beyond the npm registry.

## Licensing

Every family is under the **SIL Open Font License 1.1**, which permits
redistribution including bundled in an application, and requires the licence
travel with the files. Each `*-OFL.txt` is the upstream text, unmodified.

| Family | Weights | Copyright |
|---|---|---|
| Jost | 400–600 (variable) | The Jost Project Authors |
| Source Serif 4 | 400–600 (variable) | The Source Serif 4 Project Authors (Adobe) |
| IBM Plex Sans | 400–600 (variable) | IBM Corp. |
| IBM Plex Mono | 400, 500 (static) | IBM Corp. |

Do not delete the `*-OFL.txt` files. Shipping the fonts without them is a
licence breach, and they cost 17 KB.

## Adding a face or a weight

Edit `WANTED` in `scripts/vendor-fonts.mjs`, run it, then add the matching
entry in `src/app/layout.tsx`. Nothing in the build calls the script.
