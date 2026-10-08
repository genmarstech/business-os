"""
Django's URL prefixes, and the three other lists that have to agree with them.

═══════════════════════════════════════════════════════════════════════════════
THIS EXISTS BECAUSE A PREFIX CAN BE REGISTERED IN DJANGO AND UNREACHABLE IN
PRODUCTION, AND NOTHING ANYWHERE SAYS SO.

`/pay/`, `/sub/` and `/prc/` were in `urlpatterns` and in none of:

  · deploy/business.caddy   @api      — what Caddy proxies to Django
  · deploy/business.caddy   @django   — what gets Django's CSP
  · frontend/next.config.ts API_PREFIXES — the `next dev` rewrite

So in production those three fell through to Next, which has no such routes
and answers 404. Every screen that reads them server-side kept working, which
is what hid it: `src/lib/api.ts` reaches API_ORIGIN directly and never touches
a rewrite or a proxy rule. The browser is the half that breaks — and the till
requests an M-Pesa push from the browser, so the one broken thing was taking a
payment at a register.

The Caddyfile already carried a comment saying "keep the two lists diffable",
written after /healthz was forgotten from one of them. A comment asking people
to compare lists is not a comparison. This is.
═══════════════════════════════════════════════════════════════════════════════

It parses the two deployment files as TEXT on purpose. Importing them is not
possible for the Caddyfile and not meaningful for a TypeScript module, and the
thing being tested is what is written in the file that gets deployed — not a
model of it that could itself drift.

═══════════════════════════════════════════════════════════════════════════════
⚠ WHAT THIS CANNOT CATCH: THE HOST.

All three lists it compares are in the REPOSITORY, and `deploy/business.caddy`
is never copied onto the host automatically — that host also serves a live
client site, so the block is edited into /etc/caddy/conf.d/ by hand. A prefix
can therefore be correct in all three files here and absent in production, and
this test will pass, correctly, while the browser cannot reach it.

That happened with `/ntf/` on 2026-10-07, and the shape is worth knowing because
it is not a clean failure. `frontend/next.config.ts`'s rewrite IS LIVE in
production (its own comment used to claim otherwise), so Caddy handed the
unmatched prefix to Next and the rewrite proxied it to Django — `/ntf/unread`
and `/ntf/read` worked. Only the bare `/ntf/` broke, because Next strips the
trailing slash before applying a rewrite. One endpoint of three, which is
exactly enough for a smoke test to pass.

THE ONLY CHECK IS PROBING THE HOST AFTER A DEPLOY. A bare prefix answered by
Django returns DRF JSON with an `allow:` header and the `default-src 'none'`
CSP; answered by Next it returns a 308 that strips the trailing slash:

    curl -si https://business.genmars.co.ke/<prefix>/ | head -20

A new prefix is therefore FOUR steps, not three: this list, the two matchers,
and the host.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

REPO = Path(__file__).resolve().parent.parent.parent
CADDYFILE = REPO / "deploy" / "business.caddy"
NEXT_CONFIG = REPO / "frontend" / "next.config.ts"

# Not application prefixes, and each is excluded for its own reason rather
# than by a rule that would quietly swallow a real one.
#
#   ""        the landing page — served by Django, but it is the bare root and
#             Caddy cannot match it as a prefix
#   admin     Django's own, in every list already
#   healthz   a bare path, not a prefix, and matched without a trailing /*
NOT_AN_APP = {"", "admin/", "healthz"}


def django_prefixes() -> set[str]:
    """Every top-level prefix Django answers, from the resolver, not the text."""
    found = set()
    for entry in get_resolver().url_patterns:
        if isinstance(entry, (URLResolver, URLPattern)):
            found.add(str(entry.pattern))
    return {p for p in found if p not in NOT_AN_APP}


def caddy_matcher(name: str) -> set[str]:
    """The prefixes in one `@name path ...` line, as `pay/`, `org/` and so on."""
    text = CADDYFILE.read_text()
    line = re.search(rf"^\s*@{name} path (.+)$", text, re.MULTILINE)
    assert line, f"no @{name} matcher in {CADDYFILE}"
    return {
        token.strip("/").split("/")[0] + "/"
        for token in line.group(1).split()
        if token.endswith("/*")
    }


def next_prefixes() -> set[str]:
    text = NEXT_CONFIG.read_text()
    block = re.search(r"const API_PREFIXES = \[(.*?)\];", text, re.DOTALL)
    assert block, f"no API_PREFIXES in {NEXT_CONFIG}"
    return {name + "/" for name in re.findall(r'"([a-z]+)"', block.group(1))}


class RoutingListsAgreeTests(SimpleTestCase):
    def test_caddy_proxies_every_prefix_django_answers(self):
        missing = django_prefixes() - caddy_matcher("api")
        self.assertEqual(
            missing,
            set(),
            f"Django answers {sorted(missing)} and Caddy's @api does not proxy "
            f"them, so in production they reach Next and 404. Add them to "
            f"@api in deploy/business.caddy.",
        )

    def test_the_csp_list_covers_every_prefix_the_proxy_list_does(self):
        """
        @django is @api plus /static/*. A path on the proxy list and off the
        CSP list is a Django response served with no Content-Security-Policy
        at all, which is how /healthz was found.
        """
        missing = caddy_matcher("api") - caddy_matcher("django")
        self.assertEqual(
            missing,
            set(),
            f"{sorted(missing)} is proxied to Django and gets no CSP header.",
        )

    def test_the_dev_rewrite_covers_every_prefix_django_answers(self):
        """
        `admin/` is deliberately absent from the Next rewrite and present in
        Caddy: nobody reaches the Django admin through the dev server, and
        adding it would put an admin login behind a Next rewrite for no gain.
        """
        missing = django_prefixes() - next_prefixes() - {"admin/"}
        self.assertEqual(
            missing,
            set(),
            f"Django answers {sorted(missing)} and `next dev` does not rewrite "
            f"them, so a browser call to one fails locally. Add them to "
            f"API_PREFIXES in frontend/next.config.ts.",
        )

    def test_nothing_is_proxied_that_django_does_not_answer(self):
        """
        The other direction, and not a nicety: a prefix on the proxy list that
        Django has dropped is a hole in the web application — that path stops
        reaching Next and starts 404ing out of Django instead.
        """
        stray = caddy_matcher("api") - django_prefixes() - {"admin/"}
        self.assertEqual(
            stray,
            set(),
            f"Caddy proxies {sorted(stray)} to Django, which does not answer "
            f"it. Either the app was removed or the prefix is a typo.",
        )
