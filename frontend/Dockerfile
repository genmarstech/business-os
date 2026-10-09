# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────────────────────────────────────────
# Genmars Business Platform — the web application.
#
# Same three-stage shape as internals-tm and gen-portal's frontend, and for the
# same reasons: deps cached separately from source, no build toolchain in the
# runtime image, a non-root user that owns nothing it does not need.
#
# The user id is 10005 — 10002 is gen-portal's api, 10003 is ops, 10004 is this
# repo's Django. Distinct on purpose: shared volumes between containers are
# resolved by uid, and two services sharing one is how a permission bug becomes
# a data bug.
# ─────────────────────────────────────────────────────────────────────────────


# ── WHERE THE BASE IMAGE IS PULLED FROM, AND WHY IT IS AN ARGUMENT ──────────
#
# The default is Docker Hub, spelled in full. `node:22-alpine` and
# `docker.io/library/node:22-alpine` are the same image; writing it out is
# what lets the registry be substituted without the tag moving.
#
# ⚠ THE TAG IS NOT THE PART THAT MAY VARY. Node 22 is what ci.yml pins its
#   runner to as well, and REGISTRY must never be used to change it.
#
# CI overrides this to a read-through mirror because Docker Hub's
# unauthenticated pull limit is counted PER IP, and GitHub's hosted runners
# share theirs with everybody else building on them. That is not a
# hypothetical: it took this pipeline down twice in three minutes with
# `429 Too Many Requests`, on a branch whose tests were green — a red check
# that says nothing about the code, which the header of ci.yml argues is
# worse than no check at all.
#
# Deploys do not pass it. The host builds from Docker Hub, as it always has,
# so nothing that ships comes from the mirror; what CI proves is that this
# Dockerfile still builds, and a read-through mirror serves the same digests
# to prove it against.

ARG REGISTRY=docker.io/library

# ---- deps -------------------------------------------------------------------

FROM ${REGISTRY}/node:22-alpine AS deps

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1 \
    npm_config_fund=false \
    npm_config_audit=false

# package.json and the lockfile ONLY, so this layer survives every source edit.
COPY package.json package-lock.json ./
RUN npm ci


# ---- build ------------------------------------------------------------------

FROM ${REGISTRY}/node:22-alpine AS build

WORKDIR /app

# ── THIS IS A BUILD ARG, NOT A RUNTIME VARIABLE ──────────────────────────────
#
# next.config.ts resolves its rewrites at BUILD time and writes them into
# .next/routes-manifest.json. Setting API_ORIGIN in the runtime environment has
# no effect on them whatsoever.
#
# gen-portal shipped exactly this bug: the variable was present and correct in
# `docker compose exec web env` the entire time it was doing nothing, while
# every proxied call returned 500 with ECONNREFUSED.
#
# It is ALSO set at runtime in compose.yaml, for a different consumer:
# src/lib/api.ts reads it per request for server-component fetches. Two
# mechanisms, one value, and neither is redundant.
ARG API_ORIGIN=http://api:8020
ENV API_ORIGIN=${API_ORIGIN}

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# The theme guard runs BEFORE the build, so the failure is "you used a brand
# constant as a background" rather than a successful build of an unreadable
# page. See scripts/check-theme-tokens.mjs.
RUN npm run check:theme && npm run build


# ---- runtime ----------------------------------------------------------------

FROM ${REGISTRY}/node:22-alpine AS runtime

LABEL org.opencontainers.image.title="business-platform-web" \
      org.opencontainers.image.vendor="Genmars Tech Limited" \
      org.opencontainers.image.source="https://github.com/genmarstech/business-os"

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3030 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 10005 -S app && adduser -u 10005 -S -G app app

# `output: standalone` produces a server.js plus only the node_modules it
# actually traced. Static and public are copied separately because the
# standalone trace deliberately excludes them.
COPY --from=build --chown=10005:10005 /app/.next/standalone ./
COPY --from=build --chown=10005:10005 /app/.next/static ./.next/static
COPY --from=build --chown=10005:10005 /app/public ./public

USER 10005:10005

EXPOSE 3030

# Hits the app's own liveness route, not /. `/` renders differently for a
# signed-in caller and a stranger, and a health check that exercises a session
# path is a health check that can fail for reasons unrelated to health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:3030/healthz || exit 1

CMD ["node", "server.js"]
