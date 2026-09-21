# Identity

Who is allowed in, and on whose authority.

## Two tiers, and the line between them

| Tier | Who | Authenticated by | Credential lives |
|---|---|---|---|
| **Subscriber** | owner, org admin, accountant | Genmars account, via sign-on | gen-portal |
| **Operational** | cashier, branch manager, stock clerk | this application | here |

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
GET  /auth/me                what the caller is, and what it may touch
```

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
- A manager issuing and resetting a `StaffCredential`; `must_change_password`
  is written but nothing enforces it yet
- Binding a `StaffSession` to a `Register`, for blueprint §11 offline work
- Entitlement: this application must not take money. See the architecture note.
