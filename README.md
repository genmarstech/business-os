# business-os

The Genmars Business Platform: a multi-tenant point of sale sold to other
businesses. Backend only so far — Django + DRF at `business.genmars.co.ke`.

Registered in the Genmars system registry as `business-os`. Deployment lives
in `compose.yaml` and `deploy/business.caddy`; read the header comments in
both before changing either.

## Who signs in, and where

Two tiers of principal, and the line between them is who the commercial
relationship is with:

| Tier | Who | Authenticated by |
|---|---|---|
| **Subscriber** | owner, org admin, accountant | their **Genmars account**, through sign-on |
| **Operational** | cashier, branch manager, stock clerk | **this application**, inside their own tenant |

A tenant-local credential must never authenticate against
`api.genmars.co.ke`, and operational staff never get rows in the Genmars
`accounts.User` table. `identity/models.py` and the sibling section of the
root `CLAUDE.md` carry the full reasoning.

## Against the blueprint

`Multi_Tenant_POS_System_Blueprint.pdf` §12 sets the roadmap. V1 is complete;
V2 has started with procurement and is otherwise untouched, and the table says
so rather than letting somebody discover it.

| §9 domain | Models | State |
|---|---|---|
| Organization | Organization, Membership, Role, Permission, Subscription | **done** |
| Branches | Branch, Register, RegisterShift, StaffAssignment | **done** |
| Catalog | Product, Category, TaxRule | **done** — ProductPrice (price lists) is not built |
| Inventory | StockLevel, StockMovement, StockAdjustment, StockTransfer | **done** |
| Sales | Sale, SaleItem, Payment, Refund, Receipt | **done** |
| Procurement | Supplier, PurchaseOrder, GoodsReceipt | **done** — purchase tax is not modelled |

**V1 Core POS is complete**: organizations, branches, users/roles, products,
inventory, POS checkout, payments, customers, receipts and a basic
dashboard/reports. Subscriptions were the last thing §9 listed and did not
have — see *Paying for it* below.

**V2 is part built.** Two of its pieces exist. Closing a till by counting it
is one — `RegisterShift`, `GET /sls/reports/register-status` for the expected
drawer figure, and a cashier who cannot count their own. **Purchasing is the
other, and it is new**: suppliers, purchase orders with an approval step, and
goods receipts that move stock. See *Buying* below.

What is left of V2: cash reconciliation beyond the drawer count, the returns
workflow beyond the refund itself, advanced permissions, notifications, and
multi-branch reporting beyond the branch comparison. Nothing reports on
purchasing yet — what a supplier has cost this quarter, and what is committed
and not yet delivered, are questions this data can answer and no screen asks.

**V3 is not started**: offline mode, M-Pesa, loyalty, accounting integrations,
e-commerce sync, advanced analytics, automated replenishment. §11 asks for
idempotency keys and transaction identifiers to be defined *before* production
rollout, and they are — sales and refunds both take one — so the offline
queue has something to synchronise against when it is built.

## Paying for it

A multi-tenant POS sold to other businesses has to know whether a tenant is
paid up. `subscriptions/` is that, and the shape of it is decided by two
things it deliberately is **not**.

**It is an entitlement, not an invoice.** gen-portal already holds `Contract`,
`Invoice` and `Offer` against a client, and
`BusinessOrganization.genmars_organisation_id` says in its own comment that it
"records who we invoice, which is a different question from who may open a
till". So business-os does not grow a second invoicing system. It holds the
answer — what this tenant may do, and until when — and nothing about who was
billed, what they were charged or how they paid. Two records of one debt
eventually disagree.

**Nothing in it takes a payment.** `services.extend` records that a period has
been paid for; it does not charge anything and has no idea how to. There is no
endpoint that extends a subscription and there should not be one here: an
endpoint in the POS that moves `paid_until` grants entitlement with no money
behind it, and a subscriber's own session would be enough to use it. When the
integration is built it belongs behind a machine credential from the parent,
not behind a browser session. CLAUDE.md is explicit that no live STK push has
ever been fired from this codebase; nothing here moves that line.

### Selling never stops

```
       lapses                            nothing changes
subscription ───────▶ past due ─────────▶ sales, refunds, shifts,
                         │                stock, deliveries, reports
                      grace ends
                         ▼
                     suspended ─────────▶ adding a branch, a till or
                                          a member of staff: on hold
```

A till that refuses a sale because an invoice is late is a shop with a queue
at the counter and no way out of it from behind the till. The damage lands on
a cashier and a customer, neither of whom is party to the arrangement, and it
lands when the shop is busiest. This application already decided this once in
another costume — `must_change_password` is *asked and not required*, because
"a POS that will not open because somebody cannot think of a password at seven
in the morning is a shop that cannot sell".

So nothing a subscription does can stop a sale, a refund, a shift, a stock
movement or a delivery. What narrows is **growth**: another branch, another
till, another member of staff. Those are decisions made at a desk, they are
what a plan is actually sold by, and refusing one costs the shop nothing it is
doing today. `SellingNeverStopsTests` is the line that says so in code.

A refusal is **402 Payment Required**, not 403. 403 is a statement about the
caller's authority and is wrong here: an owner holds every permission there
is, and is being refused over an invoice. Telling them they lack permission
sends them to look at roles, which is the one place the answer is not.

### Three more decisions worth knowing

- **There is no `status` column. The state is derived from dates.** A stored
  status is only as current as the last job that ran, and the job is the thing
  that fails quietly — at which point the database says a lapsed tenant is in
  good standing and every check believes it. The facts are stored and the
  state is computed on read, so it cannot go stale, needs no cron to be
  correct, and a backup restored from last month gives today's answer.
- **A tenant with no subscription row is treated as ACTIVE, not suspended.**
  Every organisation that existed before this app has no row, and reading the
  absence as unpaid would narrow the whole customer base the moment it ships —
  a new feature behaving as an outage. New tenants get a row at onboarding, in
  the same transaction that creates the business.
- **The `Plan` table ships empty and no price is invented in code.** Charter
  04 §IV: nothing untrue on a Genmars surface. The published prices live in
  `gen-website/src/lib/company.ts` and are already duplicated into gen-portal's
  `seed_services.py`; a third copy written by whoever wrote this file would be
  a price the company never agreed to, shown to a customer. Plans are entered
  by Genmars in the admin. A tenant with no plan has no ceiling to enforce,
  which is correct for a trial and for a bespoke arrangement.

A `null` limit means **no ceiling**, never a ceiling of zero — the same shape
as `access.branch_scope` returning None for unrestricted authority, and the
same trap: a falsy check locks the largest customer out of adding a branch.
## Buying

Blueprint §9's Procurement domain, and the other end of a stock figure. Until
it existed the only way stock went UP was a manual adjustment: somebody typed
a number and the shop took their word for it.

```
Supplier ──▶ PurchaseOrder ──▶ GoodsReceipt ──▶ StockMovement(PURCHASE)
                    │
     draft ─send─▶ submitted ─approve─▶ approved ─receive─▶ part/received
                                            └──── cancel ────┘
```

Endpoints are under `/prc/`: `suppliers`, `purchase-orders` (with `submit`,
`approve`, `cancel` and `receive` actions) and a read-only `goods-receipts`.
Screens are under `/buying`.

**The approval is the point, and it is built as a permission, not an `if`.**
`purchasing.manage` raises and sends; `purchasing.approve` commits the
business to the money; `purchasing.receive` counts the delivery in. A
purchasing officer holds the first and third and not the second, a branch
manager holds the second and third and not the first — so an order is raised
by one person and approved by another, the same construction that stops a
cashier voiding their own sale. An owner holds all of them, which in a shop
whose back office is one person is the only workable answer; the control is
real where there are two people and advisory where there is one.

Three rules it shares with the sales side, deliberately, so the two money
flows do not disagree about what a document is:

- **Every cost on a line is a copy.** A supplier raising their price next
  month does not rewrite what this order committed to.
- **Nothing edits a document that has left the building.** A draft may be
  rewritten; a submitted order may only be approved, received or cancelled.
- **Numbering is per organisation** — orders from 3000, deliveries from 4000.

And three decisions worth knowing before changing it:

- **Purchase tax is not modelled.** A supplier invoice carries VAT and what a
  shop may reclaim of it is an accounting question with a filing attached;
  §12 puts accounting integrations in V3. A half-modelled input-VAT column
  would be read as an answer by whoever eventually files the return.
- **Receiving does not restate `CatalogCategoryProduct.cost_price`.** It is
  the obvious next line of code and it is wrong by default: cost_price is what
  every margin report measures against, so one delivery at a promotional price
  would silently restate the profitability of everything sold before it.
- **Over-receipt is refused, not absorbed.** Twelve arriving against an order
  for ten is either a supplier error or a cost nobody approved. Raise a second
  order for the extra, so what arrived is still explained by what was asked
  for.

A delivery writes a `PURCHASE` StockMovement and **no** StockAdjustment:
`inventory/services.adjust` writes both because a manual correction has no
other document to point at, and a delivery has one.

### Roles and permissions

`identity/access.py` holds one catalogue of 26 named permissions and the map
from roles onto it. Code asks *"may this caller refund a sale"*, never *"is
this caller a manager"* — the second question has to be re-answered in every
view the day a shop wants its accountants approving refunds, and one of those
views will be missed.

Authority arrives two ways and lands on the same catalogue: a subscriber holds
a `TenantMembership` (organisation-wide, per §2), an operational staff member
holds `staffAssignment` rows (per branch). No view asks which tier it is
talking to.

| Role | Holds | Notably does not |
|---|---|---|
| Owner | everything | — |
| Org admin | everything operational, tax included | `staff.manage`, `settings.organisation` |
| Accountant | reads the money, reports, what is on order | voids, refunds, checkout |
| Branch manager | the branch, end to end; approves and receives orders | `reports.organisation`, **`purchasing.manage`** |
| Cashier | checkout, open a shift | **voids, refunds, reports** |
| Inventory clerk | stock and transfers, receives deliveries | anything to do with sales |
| Purchasing officer | suppliers, raising orders, receiving | **approving an order**, adjusting stock by hand |
| Finance clerk | sales and branch reports, what is on order | any write |
| Branch auditor | reads the branch | every write |

The purchasing officer used to be mapped onto the inventory clerk's
permissions with the comment "procurement is V2". The role is now its own,
and it is NARROWER than that stopgap in one respect: no `inventory.adjust`
and no `inventory.transfer`. A buyer who can also move a quantity by hand can
make the difference between what was ordered and what arrived disappear
without a document.

`GET /auth/me` returns the caller's permissions so a client can draw a screen
without offering buttons the server will refuse — plus `permissions_by_branch`
for an operational principal, because a cashier at one branch and a manager at
another must not be shown a Refund button at the till where it will be denied.
**It is for drawing, never for guarding**: every endpoint checks again, since a
list returned to a browser is a list the browser can edit.

Three things are worth knowing before changing it:

- **Scope is two questions.** `tenant_scope` answers *which organisations*;
  `branch_scope` answers *which branches* and returns **None** for
  organisation-wide authority — not `[]`, which means confined to nothing.
  They are opposites and reading one as the other opens or closes everything.
- **Permissions are asked per branch where a branch is known.** One person can
  be a cashier at Westlands and manager at Karen; the union of their roles
  applied everywhere would make them a manager at Westlands. `checkout` and
  `refund` pass the branch once the shift or the request identifies it.
- **Settings is two permissions.** `settings.tax` changes what future receipts
  charge and is ordinary work an admin does; `settings.organisation` changes
  what the business *is* and stays with the owner. Neither is retroactive —
  `TaxRule.rate` is copied onto every `SaleItem` at the moment of sale, which
  is what makes granting the first one safe.

A cashier deliberately cannot void or refund. Module 6 lists "manager
approvals" beside cashier access for exactly this reason: those two are how a
till is emptied by the person standing at it, and the cheapest second person
is a permission the first does not hold.

`identity/tests/test_access.py` walks the live URL configuration and fails if a
routed viewset declares no permission, names one that is not in the catalogue,
or reaches a branch without scoping to one — because that is how this breaks:
quietly, next quarter, with every other test still green.

### The rules the sales code is built on

Worth knowing before changing any of it; each is argued at length in the file
that enforces it.

- **Nothing edits a completed sale.** `SaleViewSet` is read-only, so there is
  no PATCH on a financial record. A mistake is a void with a reason, or a
  refund that points back at the original (§10).
- **Every price on a sale line is a copy.** Raising a price tomorrow must not
  rewrite what a customer paid today.
- **Stock only moves through a `StockMovement`**, never by writing a quantity.
- **Numbering, uniqueness and reports are per organisation.** A shared
  sequence or a global constraint tells one tenant about another.
- **Tenant scope is resolved server-side, always.** A `branch` in a request
  narrows a result; it never grants access to one (§8).

## Known gaps

These are written down rather than left to be rediscovered.

- **Nothing sends mail yet, but it now can.** `Business_Platform/mail_backends.py`
  talks to Resend over HTTPS — not SMTP, because Hetzner blocks outbound SMTP
  on this host and the failure is a slow timeout that reads as "provider
  unreachable". Setting `RESEND_API_KEY` is the whole switch: present, and
  `MAILERS` points at the backend; absent, and it stays on the console, where
  `check --deploy` reports `mail.E001` truthfully. There is deliberately no
  second variable to forget, because a key set with the backend still on the
  console is a configuration that looks complete and drops every message.
  It is used by the cashier password reset below.
- **The tests run on SQLite locally.** CI now runs them against Postgres 17,
  matching compose.yaml, so the claim "the suite passes" is about the database
  the application actually ships on. A local `manage.py test` still uses
  SQLite, which is a development convenience — if a failure appears only in
  CI, that difference is the first place to look:

  ```bash
  DATABASE_URL=postgres://… virtual/bin/python manage.py test
  ```
- **`OrganizationStaff.external_user_id`** is an orphan. It defaults to a
  fresh `uuid4()`, so it never equalled an id from anywhere, and the scoping
  that once joined through it has been replaced by `identity/scoping.py`. The
  field and its comment are still there and still misleading.
- **A cashier can reset a forgotten password themselves**, by a six-digit code
  emailed to the address their manager holds. It leaves `must_change_password`
  FALSE, unlike the manager reset which sets it True — a code sent to the
  cashier's own address produces a password nobody else has seen, which is the
  point rather than a convenience. It also clears a lockout, because proving
  who you are by email and still being refused at the till is a dead end.
  **This needs `RESEND_API_KEY` set in production; it is not set yet**, and
  until it is the request silently succeeds and sends nothing.
- **`StaffCredential.must_change_password` is asked, not required.** The till
  puts the change in front of a cashier at sign-in, before a register is
  chosen, because that is the one moment they are not mid-queue — but "Do this
  later" is there. A POS that will not open because somebody cannot think of a
  password at seven in the morning is a shop that cannot sell, and refusing to
  trade is the worse failure. Deferring lasts until sign-out, so the next
  shift asks again. Making it mandatory is one early return in `Till.tsx`, and
  it is the business owner's call rather than ours.

### Closed since this list was written

Left here briefly because a gap list nobody trusts is worse than no gap list.

- ~~No Content-Security-Policy~~ — shipped `Report-Only`, then promoted to
  enforcing, in the two halves the split host needs.
- ~~There is no dashboard~~ — branches, catalogue, stock, till, sales,
  refunds, reports, buying, staff and settings all exist, and the landing
  page no longer claims otherwise.
- ~~`must_change_password` is written but never enforced~~ — the backend was
  always complete; nothing in the frontend called it. A cashier was told to
  "ask your manager to show you how", and there was no how: a manager can only
  RESET a password, which sets the flag again, so the loop had no exit.

## The frontend

`frontend/` is a Next.js application served from the **same host** as the API.
That is not a preference: `/auth/callback` sets a Django session cookie on
`business.genmars.co.ke` with no `Domain` attribute, so the browser scopes it
to that exact host. Serve the app from another origin and the cookie stops
travelling, which presents as "signing in does nothing" with no error anywhere.

Caddy splits by path — `/static/*` off disk, the API prefixes to Django on
8020, everything else to Next on 3030. The rewrites in `next.config.ts` are
for `next dev`, where there is no Caddy.

`API_ORIGIN` is set **twice and neither is redundant**: as a build arg,
because Next bakes rewrites into `routes-manifest.json` at build time; and at
runtime, because `src/lib/api.ts` reads it per request for server-component
fetches. gen-portal once shipped the half that looks fine.

```bash
cd frontend
npm run dev        # :3030, expects Django on :8020
npm run verify     # check:theme && typecheck && build — before pushing
```

`scripts/check-theme-tokens.mjs` is the same guard the other three Genmars
frontends carry. Brand constants are fixed colours; semantic tokens flip with
the theme, and using one as the other produces a light band with light text.
That shipped once already, on the marketing site.

The palette, typography and the rest of the plan are in
**[the design system spec](https://claude.ai/artifact/7pixS2nV9ArYErfB7urD1g)**.
The one rule most easily lost: **Ignition `#db7b51` is 2.75:1 on the light
ground** and must never carry small text there — accent text on light is
Mahogany at 6.02:1. On dark, Ignition reaches 5.78:1 and the tokens flip.

## Backups

```bash
./scripts/backup.sh            # dump, encrypt a copy, prune
./scripts/restore-test.sh      # restore the newest dump and check the data
```

`backup.sh` writes a `pg_dump -Fc` to `./backups/` at mode 600, and — when
`BACKUP_RECIPIENT` is set — a GPG-encrypted copy to `./backups/offsite/` for
anything leaving the host. The host holds only the **public** half of the key,
so it can encrypt a backup and cannot decrypt one: taking the server does not
hand over the archive of every earlier state of the database.

`restore-test.sh` is the half that matters. A backup nobody has restored is a
file, not a backup, and Charter 03 §IV Tier 1 asks for a *tested* restore. It
restores into a throwaway database and asserts the data is there — tables
present, organisations and migrations non-empty, and row counts for sales,
payments and refunds matching what was live when the dump began. That last one
is the check that catches a partial restore, which every other check waves
through.

### Schedule

```bash
sudo cp deploy/genmars-business-*.service deploy/genmars-business-*.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now genmars-business-backup.timer
sudo systemctl enable --now genmars-business-restore-test.timer

# An enabled timer that never fires is the usual way "we have backups" turns
# out to be false. Check that it is actually scheduled:
systemctl list-timers 'genmars-business-*'
```

Backup nightly at 02:45, restore test Sunday 04:15 — both offset from
gen-portal's 02:15 and 03:30, because the two databases share a host and
dumping them at the same moment only makes each slower. A failure raises
`genmars-alert@`, the same handler gen-portal uses, which mails with the last
25 journal lines so the alert says what broke.

### Getting the copies off the box

`gen-portal/scripts/pull-backups.sh` collects from both applications. Run it
from the laptop that holds the private key — it pulls, and the server never
pushes, so nothing that compromises the server can reach or delete what has
already been collected.

Copies land flat in `~/genmars-backups` alongside gen-portal's; the filename
prefixes keep them apart.

**Still not done:** nothing runs that pull on a schedule, and no restore test
has ever been performed against an *encrypted* copy on the machine that can
decrypt it. Until one has, the private key is assumed to work rather than known
to. Roughly monthly:

```bash
business-os/scripts/restore-test.sh ~/genmars-backups/business-<stamp>.dump.gpg
```

Two more gaps worth knowing: `sales_saleitem`, `sales_receipt` and
`inventory_stocklevel` have no `created_at`, so the completeness comparison
cannot cover them — a sale whose *items* did not restore is a gap this test
cannot see. And there is no schedule: the script exists, nothing runs it.

## Running the tests

```bash
cd backend
virtual/bin/python manage.py test          # 325 tests
virtual/bin/python manage.py test          # 332 tests
```
