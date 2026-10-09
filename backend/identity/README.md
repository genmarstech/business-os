# Identity

Who is allowed in, and on whose authority.

## Two tiers, and the line between them

| Tier | Who | Authenticated by | Credential lives |
|---|---|---|---|
| **Subscriber** | owner, org admin, accountant | Genmars account, via sign-on | gen-portal |
| **Operational** | cashier, branch manager, stock clerk | this application | here |

## One operational credential, two transports

| where | carried as | CSRF | why |
|---|---|---|---|
| a till | `Authorization: Bearer gbp_…` | **no** — nothing to forge | a fixed terminal that holds its credential explicitly and is meant to work offline (blueprint §11) |
| the office | a Django session cookie | **yes** — a browser sends it automatically | a purchasing officer, finance clerk or branch auditor reading screens on a laptop |

The argument for a bearer token is on `StaffSession`, and every word of it is
about a register. None of it reaches somebody approving a delivery in a
browser — and those three roles had nowhere to work at all, because every
office screen is server-rendered and a server component forwards cookies, not
headers.

> ⚠ **This adds a transport, not a credential store.** The two-tier rule is
> that a `StaffCredential` password must never be accepted by
> `api.genmars.co.ke` and a Genmars password must never be accepted here.
> Both doors call the same `authenticate_staff`, open the same `StaffSession`
> row and produce the same `StaffPrincipal`. A subscriber still cannot sign in
> at `/auth/staff/session`, and there is a test saying so.

One row means one revocation path: `revoke_all_sessions` ends both, and
withdrawing a login logs the browser out **on its next request** rather than
whenever it next signs in.

⚠ **A session must never hold both principals.** Both cookie classes read the
same Django session, so if both keys were set, *which* principal a request
authenticated as would be decided by the order of
`DEFAULT_AUTHENTICATION_CLASSES` — a line in `settings.py` nobody reads as a
security control. `authentication.become()` is the only thing that writes
either key: it clears both, then sets one, and cycles the session key against
fixation while it is there.

The line is **who the commercial relationship is with**. A subscriber deals
with Genmars, so Genmars holds their identity. A cashier works for the
customer, so the customer's tenant holds theirs — a shop must be able to sack
somebody at 9pm on a Saturday without telephoning us, and an offline register
cannot refresh a Genmars session.

Decided 2026-09-21. Reasoning in
`internals-tm/docs/BUSINESS-PLATFORM-ARCHITECTURE.md`; the rule and its limits
are in `CLAUDE.md`.

> ⚠ **The two credential stores must never meet.** A `StaffCredential`
> password must never be accepted by `api.genmars.co.ke`, and a Genmars
> password must never be accepted here. No shared hash, no shared table, no
> endpoint that tries one and then the other.

## Routes

```
GET  /auth/start             → redirects to app.genmars.co.ke/sign-on
GET  /auth/callback          ← Genmars sends the person back with a code
POST /auth/staff/sign-in     {organization, username, password} → token
POST /auth/staff/sign-out
GET  /auth/me                what the caller is, what it may touch, where it works

POST /auth/staff/credentials/            {staff, username} → login + emailed code
POST /auth/staff/credentials/{id}/invite/  send that email again
POST /auth/staff/password/reset          {organization, username} → a code, maybe
POST /auth/staff/password/reset/confirm  spend it; 201 + session on a first setup
```

## Somebody's first day

A manager creates the login and **does not create a password for it**.

```
POST /auth/staff/credentials/  {"staff": 12, "username": "jmwangi"}
```

With no `password`, the credential is saved with an unusable one — nobody can
sign in as them, including the manager who just made it — and the employee is
emailed their username, the **business number** and a setup code. They tap
"First time here?" at the till, spend the code, choose a password nobody else
has ever seen, and are **signed straight in to their workspace**.

Passing a `password` still works, because an employee with no email address on
file still has to be able to get on a till. That is the case
`must_change_password` was written for, and it is now the exception rather than
the default.

| | first-time setup | forgotten password |
|---|---|---|
| minted when | the login has no usable password | it has one |
| lasts | 72 hours (`SETUP_CODE_LIFETIME`) | 15 minutes |
| the email says | "you have been set up to work at…" | "someone asked to reset…" |
| on success | 201 with a session — straight to work | 204; they sign in with it |
| `must_change_password` after | false — they chose it | false |

The purpose is read off the credential in `services.request_password_reset`
and is **never a parameter**. That is what makes 72 hours defensible: a
long-lived code cannot be minted against a live login, and choosing a password
closes the setup state so nothing can mint a second one. Guessing is bounded by
`MAX_ATTEMPTS` per code, not by the window.

> ⚠ The public endpoints answer identically whatever happened, and that now
> includes **not naming the window** — a response that said "15 minutes" would
> tell anybody who typed a guess whether that username has ever been signed
> into. The email states it, and the email goes to the person it concerns.

## Where a signed-in person is sent

`access.workspaces(principal)` is the one answer, ordered, returned by both
`/auth/staff/sign-in` and `/auth/me`. The till used to derive it from the
permission list and got it wrong for every role it was not written for.

| role | lands on |
|---|---|
| cashier, sales associate | `["sell"]` |
| assistant manager | `["sell", "count"]` — asked which |
| inventory clerk | `["count"]` |
| purchasing officer, finance clerk, branch auditor | `[]` |

**Empty is a real answer and not a refusal.** Those three hold permissions the
API honours — `purchasing.receive`, `reports.branch` — and have no till screen
yet. The till says so plainly; it must never render empty as a failed sign-in.
Every name in `WORKSPACES` must have a screen behind it, and
`test_first_login.py` fails if a role is added without a decided landing.

## Configuration

The client secret is a credential and is **not** in `settings.py`. It is shown
exactly once when issued in ops → Settings → Engineering → Sibling sign-in, and
can only be replaced, never recovered.

```bash
GENMARS_SIGN_ON_CLIENT_ID=gsso_…
GENMARS_SIGN_ON_CLIENT_SECRET=gsec_…
GENMARS_SIGN_ON_REDIRECT_URI=https://business.genmars.co.ke/auth/callback
GENMARS_PORTAL_ORIGIN=https://app.genmars.co.ke   # default
GENMARS_API_ORIGIN=https://api.genmars.co.ke      # default
```

`GENMARS_SIGN_ON_REDIRECT_URI` must match what is registered in ops **exactly**
— the portal compares the whole string, never a prefix, because prefix matching
is how `https://business.genmars.co.ke.attacker.com/…` gets accepted. It must
also be live https: the portal refuses localhost, IP literals and reserved TLDs,
because a redirect address is where somebody holding a code that becomes their
identity gets sent.

**That means the flow cannot be exercised from a laptop.** Point a staging host
with a real certificate at your development machine, registered as its own
`SignOnApp` with its own secret, so nothing about production is shared with it.

## What a view must do

Knowing *who* is calling and knowing *what they may see* are different
questions. Authentication is now closed by default — `REST_FRAMEWORK` defaults
to `IsAuthenticated`, so a new viewset is shut until somebody opens it — but a
view still has to scope its queryset:

```python
from identity.permissions import scoped

class ProductViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return scoped(Product.objects.all(), self.request.user)
```

`scoped()` is the only helper of its kind on purpose, so "how does isolation
work here" has one answer and one place to audit. A row belonging to another
tenant comes back **empty**, never forbidden — a 403 confirms the row exists,
which is the same leak wearing a different status code.

Never read an `organization_id` from a request body and trust it. That is
blueprint §8, and `tenant_scope()` is the answer to it.

## What is deliberately thrown away

The sign-on token carries `is_staff` and `staff_role`. They describe somebody's
standing **at Genmars** and mean nothing inside a customer's shop. They are not
stored, not returned and not consulted — granting on them would hand every
Genmars employee the till of every customer.

The token's `organisations` array is a Genmars membership list. It is an
onboarding hint ("you already deal with us as Kilimani Dental — is this that
business?") and never a grant. Authority comes from `TenantMembership`.

## Standing up a business

`POST /org/organizations/` is the one endpoint reachable with an empty scope,
because a subscriber arriving from Genmars for the first time has no membership
anywhere and every other list is empty for them until they have one.

It creates the organisation **and** the creator's `OWNER` membership in one
transaction. An organisation saved without a membership is not "a tenant
awaiting setup" — it is an orphan, invisible to its own creator and to
everybody else, reachable only from the admin. Either both rows exist or
neither does.

- **Only subscribers.** A till gets 403: a cashier signed in at one shop
  standing up a second business, which they would then own, is not a feature.
- **Capped at 3 per account** (`services.MAX_TENANTS_PER_ACCOUNT`). There is a
  real case for two and no honest case for forty; self-serve creation with no
  ceiling is a spam vector that costs nothing to open and something to clean up.
- The sign-on callback returns `needs_a_business` so a client can send a
  first-time subscriber here, and `genmars_organisations` so the screen can ask
  "you already deal with us as Kilimani Dental — is this that business?"
  rather than making somebody retype a name we know. **Answering yes writes
  `genmars_organisation_id`, which records who we invoice and grants nobody
  anything.**

## Still to build

- Inviting another subscriber
- Binding a `StaffSession` to a `Register`, for blueprint §11 offline work
- Entitlement: this application must not take money. See the architecture note.

### No longer outstanding

**The purchasing officer, the finance clerk and the branch auditor now have
somewhere to work**, and it was not a till workspace. All three landed on a
screen saying we had not built theirs, and the answer turned out to be that we
had: every office screen is server-rendered and already permission-gated —
`Shell.tsx` hides a navigation item the caller has no permission for, and half
a dozen pages branch on `me.kind === "staff"`. They simply could not reach it,
because a server component forwards cookies and they held only a bearer token.

So there is a second transport for the same credential —
`StaffSessionAuthentication`, a cookie session opened at
`POST /auth/staff/session` — and the till now points them at `/sign-in`
instead of apologising.


`must_change_password` said "nothing enforces it yet", and the answer turned
out not to be enforcement. A login created with no password at all cannot be
used until its holder sets one, so the flag has nothing to enforce on the
default path — it now marks only the exception, where a manager typed a
password because there was no address to email.

## Running it

```bash
cp backend/.env.example backend/.env    # fill it in; never commit it
set -a && . ./backend/.env && set +a
docker compose up -d --build
docker compose exec api python manage.py migrate
```

Postgres, not SQLite — settings refuse to boot outside DEBUG on SQLite, because
two tills writing at the same moment is the normal case for a POS rather than
an edge case.

The API is published on `127.0.0.1:8020` only. `deploy/business.caddy` is the
single public way in, and it needs a `sudo cp` onto the host:

```bash
sudo cp deploy/business.caddy /etc/caddy/conf.d/business.caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
systemctl is-active caddy
```

Installing that block is what triggers certificate issuance — until it exists,
`business.genmars.co.ke` resolves, 308s to https and then fails TLS, because
Caddy has no certificate for a name it has never been asked about.

### The order matters

1. `docker compose up` — something must answer on 8020 or Caddy serves 502
2. the Caddy block — the certificate is issued on reload
3. register the app in ops → Settings → Engineering → Sibling sign-in, with
   redirect `https://business.genmars.co.ke/auth/callback`
4. put the client id and secret into `backend/.env` and restart the api

Step 3 is founder-only and the secret is shown **once**.
