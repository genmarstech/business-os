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
| Catalog | Product, Category, TaxRule, PriceList | **done** |
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

**Purchasing now reports on itself** — what each supplier has cost, what is
committed and not yet delivered, and which of them turn up when they said
they would. See *What the buying reports will and will not say* below.

**Notifications are built** — stock crossings, M-Pesa outcomes, approvals
waiting and short drawers, addressed to a permission rather than to a person.
See *Telling somebody something happened* below.

**Everybody the role table names can now get in.** The purchasing officer, the
finance clerk and the branch auditor hold real permissions and had nowhere to
use them: the office is server-rendered and they carried a bearer token, which
a server component never sees. The same credential now also opens a cookie
session at `/sign-in`, so the screens that were already written and already
permission-gated are reachable by the people they were gated for.

**Returns are now taken at the till**, which is where a customer with a
receipt actually stands. `refund_sale` had worked since module 9, but only
from the office — so in practice a manager recorded a `pay_out` through Cash
in/out, which balances the drawer and writes no refund document: nothing goes
back into stock, the sale still reads as fully paid, and the same line can be
returned again tomorrow. Closing it needed two things underneath. `Refund`
learned **how the money went back**, because it cannot be inferred — a shirt
bought on M-Pesa is routinely refunded in cash — and the drawer learned to
count cash refunds, having previously read short by every return taken. The
second was the same bug `CashMovement` was written to fix, arriving the same
way. See *Taking a return at the till* in `till/Return.tsx`.

What is left of V2: cash reconciliation beyond the drawer count and the
mid-shift movements, and multi-branch reporting beyond the branch comparison.

**V3 is barely started, and M-Pesa is the piece that is done** — STK push at
the till, a public callback that decides nothing, and a payment that can be
spent exactly once. See *Taking M-Pesa at the till* below. The rest is
untouched: offline mode, loyalty, accounting integrations, e-commerce sync,
advanced analytics, automated replenishment. §11 asks for
idempotency keys and transaction identifiers to be defined *before* production
rollout, and they are — sales and refunds both take one — so the offline
queue has something to synchronise against when it is built.

## What things cost

Blueprint §9's `ProductPrice`, and the last structural gap in the domain
table. Until it existed a product had exactly one price, and the only way to
run a promotion was to edit it and remember to edit it back.

A **price list** is a named set of prices, in force at some branches (or all),
between some dates (or always). `/ctl/price-lists/`, screen at
`/catalogue/prices`.

### One implementation, or the shelf edge and the till disagree

`catalog/pricing.py` is the only place a price is decided. The product
endpoint calls it, `sales/services.checkout` calls it, and nothing else works
a price out at all.

That is the whole point of the module. A till that priced off `selling_price`
while the server charged a promotion is a customer being charged something
other than what they were quoted, at a counter, with nobody in the shop able
to say who is right. `SamePriceEverywhereTests` reads the price from the API
and then rings up a sale, and asserts the same number.

The checkout used to read `product.selling_price` **twice** — once for the
line total and again when writing the `SaleItem`. Those agreed only because
they were the same attribute; with a list in play they are two resolutions of
a question that can move, so there is now one, carried through.

### The rule

A list applies when it belongs to the organisation, is active, names this
branch *or no branch at all*, and the day is inside its window *or it has no
window*. Applying lists are walked **highest precedence first**, and the first
one holding an entry for that product wins.

Not the highest-precedence list alone: a promotion naming three items must not
blank out the prices of the four hundred it does not mention.

**Precedence is unique per organisation, enforced by the database.** Two lists
can easily both apply — a branch's own prices and a promotion running
everywhere — and something has to decide. If that something were row order, a
till and a receipt printed a second apart could disagree and the shop would
have no way to find out why. A tie cannot be stored, so a tie cannot be broken
arbitrarily at read time; setting precedence is a conversation somebody has
once, when they make the list.

Nothing applies → the product's own `selling_price`, which remains the answer
in the overwhelming majority of shops.

### Four more decisions

- **A list never removes a price.** There is no way for one to withdraw a
  product from sale — that is `is_active` on the product, it is one switch,
  and splitting it across two concepts is how a shop ends up unable to work
  out why something will not scan.
- **A row is the override, so zero means zero.** A giveaway is a real thing a
  shop does; encoding "no override" as `0.00` would make the two
  indistinguishable. Negative is refused — a price below nothing pays the
  customer to take the stock.
- **Branches are a join table, not a nullable column.** "This promotion runs
  at Westlands and Karen but not Kisumu" is an ordinary thing to want and one
  foreign key cannot say it. No rows at all means everywhere.
- **The sale records which list priced it.** `SaleItem.price_list`, PROTECT.
  "Why was this 80 when the shelf says 100" is asked weeks later, by which
  time the promotion has ended and the configuration explains nothing. Lists
  are deactivated, never deleted, for the same reason.

Resolution is **bulk-first**: a till opens with the whole catalogue on screen,
so `prices_for` takes every product at once and costs two queries whatever the
count. `price_for` is a thin wrapper over it rather than the other way round,
and a test asserts the query count does not move with the catalogue.

Editing a list changes tomorrow's prices and rewrites nothing. Every price on
a sale line is a copy — the same snapshot rule that keeps a supplier's price
rise out of last quarter's margin.

## Taking M-Pesa at the till

A cashier types the customer's number, the customer gets a prompt, and the
sale rings itself up when Safaricom confirms it. `payments/`, screens at
`/settings/mpesa` and inside the till.

**These are the tenant's payments, not Genmars'.** A customer pays the SHOP,
on the shop's own paybill, through the shop's own Daraja application. No money
passes through Genmars and no Genmars credential is involved. What a tenant
pays Genmars for their subscription is a separate flow, brokered by gen-portal
where the `Invoice` and the company's M-Pesa credentials already are — keeping
those apart is why this app holds no Genmars secret.

### The callback is public, so it decides nothing

Safaricom post the result to a URL we publish, and a payment processor cannot
hold a session, so that URL is reachable by anyone. If a callback saying
"paid" were enough to mark a sale paid, the endpoint would be a way to walk
out of the shop with the stock.

So **the STK query is the source of truth and the callback is only a hint.** A
callback with a valid token causes us to ask Safaricom directly; nothing in
its body is parsed, stored or believed. A forged callback costs one outbound
query and achieves nothing — `test_a_forged_callback_cannot_mark_a_push_paid`
posts a perfectly-formed success while Safaricom says cancelled, and the push
must come out FAILED.

It also makes the feature work when the callback never arrives, which happens
often enough to design for: the till polls, and the endpoint it polls asks
Safaricom every time.

Other decisions in that endpoint: an unknown token is answered **200**, not
404 — a 404 tells a prober which tokens are real, and Safaricom retry anything
that is not a 200 for hours. The token is 32 random bytes, stored as a SHA-256
digest, so a database read does not hand anybody a working callback URL.

### A confirmed payment is spent exactly once

`StkPush.sale` is a OneToOne and the database enforces it. The push is spent
**inside the transaction that writes the sale** — `sales/services.checkout`
calls `payments.services.spend`, and a refusal rolls the whole sale back,
stock included. Two separate requests would leave a window where the sale
exists and the payment is still unspent, and an unspent confirmed payment can
fund somebody else's basket. `spend` also refuses a wrong amount, an
unconfirmed push, and one belonging to another business.

The sale is written **after** the money, not before. Creating it first would
mean voiding one every time a customer changes their mind at the PIN prompt,
and a void is something a manager has to explain.

### Credentials, and the dependency that stores them

A Daraja consumer key, secret and passkey belong to the **customer**. Holding
them makes this database worth breaking into for a reason unrelated to the
shop's own data, so they are encrypted rather than merely access-controlled.

That needed `cryptography` — **Charter 03 §I's first exception in this
repository**. The standard library authenticates and generates but ships no
symmetric encryption, and hand-rolling AES-GCM over a KDF is exactly the code
that should not be hand-rolled. The alternative was other businesses' merchant
secrets in plaintext in a Postgres dump.

The key is `MPESA_CREDENTIAL_KEY`, deliberately **not** derived from
`DJANGO_SECRET_KEY`. Rotating the secret key is the ordinary response to
thinking it may have leaked, and its documented cost is invalidated sessions —
if merchant credentials hung off it, rotation would also turn every tenant's
M-Pesa configuration into noise, discovered at a counter. Absent, the feature
is honestly off: the configuration endpoint answers 503 and nothing is stored
in the clear. The three secrets are write-only in the API and never returned,
masked or otherwise.

### Two smaller decisions

- **Cents are refused, not rounded.** M-Pesa moves whole shillings. Rounding
  down leaves the drawer short on every such sale and never reconciles;
  rounding up charges more than the receipt says. So a basket of 150.50 is
  refused with a message, and a shop that wants to round records it as a
  discount.
- **There is no "mark as paid" button at the till.** A cashier who could say
  the money arrived is one who can be talked into it by a customer holding a
  convincing SMS.

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

### Recording that somebody has paid

`services.extend` existed and **nothing called it**. A customer could pay by
bank transfer, by M-Pesa to the company till, in cash — and Genmars had no way
to write it down, so the trial lapsed and the subscription suspended anyway.
The product was not chargeable, not because the money could not be taken but
because its arrival could not be recorded.

It is an admin action now, on `Subscription`: select the tenants, give the date
the money covers, and the services write the change and the event beside it.
Taking the money is still not this application's job and deliberately never
will be.

The admin also **used to allow exactly what the API forbids.** Every field was
editable, so `paid_until` could be moved eighteen months out by typing in a box
and the append-only log would say nothing happened — the one surface Genmars
actually uses was the one that bypassed the rule. Every commercial field is
read-only now, and the only way to move one is an action that writes an event
naming who did it.

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
`approve`, `cancel` and `receive` actions), a read-only `goods-receipts`, and
`reports` (`overview`, `by-supplier`, `by-product`, `outstanding`,
`reliability`). Screens are under `/buying`.
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

### What the buying reports will and will not say

`/buying/reports`, served by `procurement/reports.py`. Three decisions in it
are worth more than the arithmetic:

- **Ordering is a commitment; receiving is a cost, and the two are never
  added together.** "What has this supplier cost us" is answered by what
  actually arrived — a goods receipt is the moment a liability becomes real.
  Reporting ordered value as spend overstates every quarter with stock in
  transit, and overstates it for ever wherever an order was cancelled. Both
  figures appear, labelled, the same way the sales reports put refunds beside
  revenue instead of netting them off.
- **"Outstanding" takes no window, and that is not an oversight.** Everything
  else is an aggregate over a period; this is a position — what is owed right
  now. "What was outstanding during September" is not a question with one
  answer, and a date filter would quietly produce a number that looks like
  one. It also starts at `submitted`, not `draft`: nobody outside the shop
  has been asked for a draft, so counting one as money committed would let an
  abandoned shopping list sit in the obligations figure for ever.
- **Supplier reliability reports what it could not judge.** An order still in
  transit has no verdict yet, and one raised with no expected date cannot be
  judged at all. Both are counted and shown rather than dropped, so a 100%
  figure computed from three of a supplier's forty orders says so on its
  face. The average lateness is over the late orders only — averaging the
  zeros of the on-time ones into it produces a small, reassuring number for a
  supplier who is occasionally catastrophic.

Window parsing, two-decimal quantisation and the Decimal-to-string walk moved
to `Business_Platform/reporting.py` when this was written. They were in
`sales/reports.py`, which was right while sales was the only thing with a
dashboard — buying should not have to depend on selling for the meaning of
"this month".

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

> **The till's Return screen does not bend that, and it is worth saying why,
> because it looks like it does.** The button is offered to every cashier
> and it is not gated on `sales.refund`. What the cashier does with it needs
> no permission they lack: find the sale, count what came back, say whether
> it goes on the shelf and why. The single request that gives money back
> carries a manager's own credential, entered on the terminal and used once —
> and the server checks `access.may(…, SALES_REFUND, branch)` on whatever
> actually arrives, so the prompt is an arrangement and the refusal is the
> control. Hiding the button would only return the cashier to sending the
> customer away to find somebody, which is what the screen replaces.
>
> Three tests in `sales/test_returns.py` are what keep this honest: a
> cashier's token is refused, a manager's is accepted, and a manager cannot
> record the refund against the cashier. If the first of those ever passes
> for the wrong reason, the manager prompt is decoration.

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

## Counting the shelves

Five comments in this codebase justified how carefully a movement is written
by appealing to a stock take — *"a stock take six months later can walk every
unit back"*, *"the ones a stock take is reconciled against"* — and there was
no stock take. `REASONS` in `inventory/services.py` has carried
`COUNT: "A count disagreed with the system"` the whole time with nothing
producing it. The discipline was being paid for and never cashed in.

A count is opened on a branch, lines are recorded, and it is closed. It is the
inventory half of what `branches/services.py` already does for cash: count the
thing, compare it with what the system believed, record the variance, close it
so the number means something afterwards.

### Expected is read when the line is counted

Not when the count opens, and not at close. **A shop keeps trading while
somebody counts it.** Reading the expected figure at close folds every sale
made during the count into the variance, and the stock take reports the
afternoon's trade as missing stock — which is the error that makes a shop
distrust its own counts and stop doing them. Snapshotting at open is wrong
from the other end.

Taken at the instant of counting, the pair is true: this is what was on the
shelf and this is what the system believed, both at one moment. Sales after
that are real movements and apply on top. It is the same rule as every price
on a document line here — a copy, never a join.

The test that pins it counts 48 against a system saying 50, sells ten more
while the count is open, and asserts the shelf ends at 38. It also asserts the
two wrong answers: **48**, from overwriting the quantity with the counted
figure, and **32**, from deriving the variance at close against the live
quantity.

### Closing books the difference through `adjust`

Stock only ever moves through a `StockMovement`. A stock take is the operation
most tempted to break that rule, because it already knows the number it wants
the quantity to be — and a count that assigned `quantity = counted` would
leave the one movement nobody can explain sitting in the middle of the trail
every other movement was written to preserve.

Each line with a variance books a delta with reason `COUNT`, exactly as a
manager correcting one shelf by hand would. A line that agreed books nothing
and points at no movement.

Closing is **not reversible**, for the reason `close_shift` is not: a count
that can be reopened and recounted is a count whose variance means nothing,
because the second number is always the one that agrees. A miscount is
corrected by a new count, which is its own record.

### Counting and closing are two permissions

`INVENTORY_COUNT` opens a count and writes down what is on a shelf.
`INVENTORY_COUNT_CLOSE` books what it found.

The write-off is where a shortfall stops being a question, and the person who
counted the shelf is the last one who should settle it alone. It is the split
`PURCHASING_APPROVE` makes and the one `SALES_VOID` makes at the till: the
second person is a permission the first does not hold. An inventory clerk
holds `COUNT` and not `CLOSE`; a branch manager holds both.

### Three smaller decisions

- **One open count per branch**, enforced by a partial unique constraint as
  well as a sentence in the service. Two people counting the same shelves
  produce two contradictory truths and no way to say which was first. Closed
  and abandoned counts do not block the next one.
- **Abandoning requires a reason.** An abandoned count is the one somebody
  will ask about later — half the shop counted and then nothing happened —
  and "no reason given" is the answer that makes them ask again.
- **The summary counts lines, not the catalogue.** A partial count is the
  ordinary case, one aisle on a Tuesday, and reporting it against every
  product the branch stocks would make every count look unfinished.

## Starting somebody on a till

A manager creates the login and **does not create a password for it**:

```
POST /auth/staff/credentials/  {"staff": 12, "username": "jmwangi"}
```

With no `password` the credential is saved with an unusable one — nobody can
sign in as them, including the manager who just made it — and the same request
emails the person their username, the **business number** and a setup code.
They tap "First time here?" at the till, choose a password, and are **signed
straight in to their workspace**. `POST /auth/staff/credentials/{id}/invite/`
sends it again, for a bounce or somebody who never got round to it.

A first-time code lasts **72 hours** where a forgotten-password code lasts
fifteen minutes, because a manager sets somebody up on Friday for a Monday
shift. The window is bounded by state rather than the clock: the purpose is
read off the credential in `services.request_password_reset` and is never a
parameter, so a long-lived code cannot be minted against a live login, and
choosing a password closes the setup state. Guessing stays bounded by
`MAX_ATTEMPTS` per code.

Passing a `password` still works, because an employee with no email address on
file still has to get on a till — and that path also works on a login already
awaiting setup, which is what stops mail trouble from keeping somebody off a
register at seven in the morning.

**No email ever carries a password, and that is the whole design.** The
alternative a manager reaches for is to type one and send it over WhatsApp,
at which point two people know it and nothing that cashier rings up is solely
theirs until they change it — and `must_change_password` is *asked, not
required*, so "later" is a button people press. The invitation uses the same
single-use code machinery as the forgotten-password flow — the same table and
the same attempt ceiling, differing only in the window above — so the password
is chosen by the person who will type it and seen by nobody else. There is a
test asserting the message contains no password.

It also **says why it could not send**, unlike `StaffPasswordResetView`,
which answers identically whatever happens. That uniformity exists so an
anonymous caller cannot learn whether a username exists; it does not apply to
a manager holding `staff.manage` asking about their own employee, whose
record they are looking at. "Jane has no email address on file" costs nothing
and saves somebody staring at a screen that claims to have sent something.

The hourly ceiling on reset codes applies, so a mistyped address cannot be
turned into a hundred emails at a stranger — reported as a 429 rather than
silently swallowed.

## Cash in and out of a drawer

Expected cash used to be opening float plus cash taken less change given. A
shop that lifts KSh 5,000 out at lunchtime to walk it to the bank then counts
a drawer 5,000 below what the system expects, and the till reports it
**short** — for the most ordinary thing a cash business does.

The product already knew it happened: `RegisterShift.note`'s own help text
offers *"a float taken for change"* as an example. The answer was prose in a
box nothing adds up. And once short drawers started raising a notification to
every holder of `reports.branch`, the cost stopped being a wrong figure on a
screen and became **a manager paged, daily, about money nobody lost**.

`CashMovement` records it instead: a kind, a positive amount, a required
reason, and who. Append-only — a movement recorded wrongly is corrected by a
second one in the opposite direction, for the reason §10 gives about sales.

### In and out are not the same authority

| | effect on the expected figure | needs |
|---|---|---|
| change brought in | **raises** it | `shift.open` — a cashier's own act |
| dropped to the safe / banked | **lowers** it | `shift.close` — a manager |
| paid out of the drawer | **lowers** it | `shift.close` — a manager |

The arithmetic is the argument. A false pay-in makes the drawer look **more**
short, never less, so it hides nothing. A pay-out lowers what is expected — a
cashier who could record one could take money and write the shortfall away in
the same movement, which is precisely what a drawer count exists to catch. The
same shape as voiding a sale and approving a purchase order: the second person
is a permission the first one does not hold.

The viewset's `permissions` map is keyed by action and cannot say "depends on
the body", so the action is registered at the **weaker** of the two and the
stronger is checked by hand — registered rather than omitted, because an
action missing from that map inherits `default_permission` silently.

The till explains the refusal **before** the button is pressed rather than
after a 403, because "ask your manager" is something somebody at a counter can
act on.

> ⚠ **Nothing moves through a closed shift.** A movement recorded afterwards
> changes the expected figure after somebody counted against it, rewriting a
> variance that has already been signed off.

### It also made an old claim true

`drawer()` carried a banner reading *"ONE IMPLEMENTATION, SHARED WITH THE
REPORT"* — and it was not shared. `sales/reports.register_status` held a
second copy of the same arithmetic. They stayed in step only because nobody
had changed either, and then cash movements were added to one: a shop that
banked at lunchtime would have seen the close screen and the dashboard expect
figures hundreds of shillings apart, with nothing to say which was lying.

That is the exact failure the banner describes, reached the exact way it
predicted. The report now calls `drawer()`, and a test asserts the two agree
field by field.
## Three doors, and who goes through which

| who | where | credential |
|---|---|---|
| the owner, an admin, an accountant | `/auth/start` → Genmars | a **Genmars account** |
| a cashier, a stock clerk | `/till` | a **till login**, as a bearer token |
| a purchasing officer, finance clerk, branch auditor, branch manager | `/sign-in` | **the same till login**, as a cookie session |

The third door is new, and it closed a hole the role table had been promising
around. Those roles hold real permissions — `purchasing.approve`,
`reports.branch` — against endpoints that honour them, and they had nowhere to
use them: every office screen is server-rendered, and a server component
forwards cookies rather than headers, so somebody holding only a bearer token
could not reach a single one. They signed in at the till and were told their
screen did not exist.

**Nothing new had to be built behind that door.** `Shell.tsx` already hides a
navigation item the caller has no permission for, and half a dozen pages
already branch on `me.kind === "staff"`. The application was written for them
and could not let them in.

> ⚠ **A transport, not a credential store.** Both staff doors call the same
> `authenticate_staff`, open the same `StaffSession` row and produce the same
> `StaffPrincipal` — so `revoke_all_sessions` ends both and withdrawing a login
> logs the browser out on its next request. A `StaffCredential` password is
> still refused by `api.genmars.co.ke`, and a Genmars password is still refused
> here. `backend/identity/README.md` has the full argument.

The cookie door **enforces CSRF** and the token door **must not**: a browser
sends a cookie automatically so a cross-site POST would ride on it, while a
header is never sent automatically and asking a till for a CSRF token would
break every register. That is why they are two authentication classes rather
than one with a branch — a branch is a thing that can be wrong, and this one
would fail open.

## Scanning a barcode

`CatalogCategoryProduct.barcode` is unique per organisation, with empties
excluded from the constraint — loose goods and anything sold by weight have no
barcode, and that is ordinary rather than exceptional.

Three ways in, and they resolve a code identically because the till shares one
`resolve()` between them:

| | needs | works on |
|---|---|---|
| **wedge scanner** | nothing — it is a keyboard that types fast and presses Enter | every device |
| **typing** | nothing | every device |
| **phone camera** | `BarcodeDetector` + a secure context | see below |

**A barcode is matched exactly; the SKU is the fallback.** A partial match on
thirteen digits is a different product, and the wrong line in a basket is
money. The SKU is tried second because a shop printing its own labels for
loose goods prints the SKU on them, in Code 128 rather than EAN.

### The camera is Chromium-only, and it is never a silent failure

`BarcodeDetector` is built into Chromium, so camera scanning costs the bundle
nothing — Charter 03 §I, and the alternative is a few hundred kilobytes of
WebAssembly in a till that exists to keep up with a queue.

> ⚠ **Safari cannot do this, and that means every browser on iOS.** Every iOS
> browser is WebKit underneath, Chrome for iOS included, so this is not "install
> Chrome" — it is iPhones, full stop. Android Chrome is the overwhelming
> majority of phones in this market, which is why the trade is this way round
> today.

`barcode.ts` answers the capability question in one place, and it is asked
before a Scan button is drawn — so where the answer is no the **button is
absent rather than disabled**, because a permanently dead control invites
somebody to keep pressing it. Typing and wedge scanners still work there, and
the field beside it says so. If iPhones start mattering, the fix is a lazily
imported wasm decoder behind that same function and nothing above it changes.

It also checks `window.isSecureContext`, because `getUserMedia` is refused over
plain http — a till reached by IP address on a shop's LAN would otherwise offer
a Scan button that always fails.

### Selling and counting scan the same way and end differently

The register **keeps the camera open**: a basket is six things and closing
between each would make scanning slower than typing. A stock take **closes on
a hit**, because the next thing in an aisle is typing how many are on the
shelf, and a live preview would cover the number pad.

A stock take resolves against `BranchInventory` rather than the catalogue, so
`product_barcode` is read through the join — one product on four branches'
shelves is the same digits, and a duplicated column would be four places to go
wrong the day a code is corrected. `test_barcode.py` asserts the query count
does not grow with the row count, which is how a dropped `select_related`
would show.

Two things the camera does deliberately: the **same code is ignored for 1.5
seconds** after it is accepted, because the detector reads the same label on
every frame and one scanned item would otherwise become thirty; and a code
nothing matches **says so** rather than staying silent, because silence is
indistinguishable from a camera that did not read the label, so somebody
rescans the same thing instead of learning the product has no barcode on file.

The camera is stopped on every path out. A stream whose tracks are left running
keeps the sensor powered, the recording indicator lit and the battery draining
on a phone somebody holds for a whole shift — and it looks exactly like a
shop's own software watching its staff, which is a thing nobody will ask us
about before deciding.

## Telling somebody something happened

`backend/notifications/` — a feed, an unread count, and a bell at the bottom of
the sidebar. `notifications/README.md` has the reasoning; the three things worth
knowing from out here:

**A notification is addressed to a permission and a scope, never to a person.**
Two kinds of principal and an audience that changes after the event make a
recipient column unworkable: a row naming somebody keeps notifying them after
they leave the rota and never reaches whoever was promoted this morning.

**Stock is written in three places and the sale is the one that matters.** Stock
runs low *because of selling*, so a low-stock notification wired only to the
adjustment screen fires for corrections and stays silent for the shop actually
running out. All three paths are tested through their real service functions.

**It is polled, because gunicorn runs three synchronous workers.** A held-open
SSE connection occupies one for its life, so three tills and a laptop would
exhaust the pool and the API would stop answering mid-sale. Charter 03 §I over
an async worker class and a fan-out dependency; `/ntf/unread` is a COUNT over an
index, and the browser stops asking while the tab is hidden.

⚠ Every raise is wrapped so it **cannot fail a sale** — which means a broken
notification is invisible to the person who should have received it. That is why
every test in that app asserts a row exists rather than that nothing threw.

## Known gaps

These are written down rather than left to be rediscovered.

- **The till has no notification bell**, on purpose: it is a terminal rather
  than a dashboard, and a cashier's two relevant notifications are already in
  front of them — a payment's outcome is on the screen they are watching, and
  they cannot order stock. Operational staff who work in the OFFICE do see the
  bell now, since they reach the Shell like anybody else.
- **Camera scanning does not work on iPhones.** `BarcodeDetector` is
  Chromium-only, and every iOS browser is WebKit underneath — so this is
  iPhones rather than a browser choice. Typing a code and USB wedge scanners
  work everywhere, and the Scan button is simply absent where the camera
  cannot be used, so it fails visibly rather than silently. The fix, if it
  becomes worth its weight, is a lazily imported wasm decoder behind
  `barcode.ts`'s `scanning()`; see "Scanning a barcode" above for why that
  dependency has not been taken yet.
- **Nothing sends mail yet, but it now can.** `Business_Platform/mail_backends.py`
  talks to Resend over HTTPS — not SMTP, because Hetzner blocks outbound SMTP
  on this host and the failure is a slow timeout that reads as "provider
  unreachable". Setting `RESEND_API_KEY` is the whole switch: present, and
  `MAILERS` points at the backend; absent, and it stays on the console, where
  `check --deploy` reports `mail.E001` truthfully. There is deliberately no
  second variable to forget, because a key set with the backend still on the
  console is a configuration that looks complete and drops every message.
  ⚠ It is now load-bearing for ONBOARDING, not only for recovery: a till login
  created without a password can only be opened with the setup code it emails.
  A host without working mail can still put somebody on a till — the manager
  types a password — but a shop doing that has lost the property the whole
  flow exists for.
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

- **An un-caught 500 now reaches a person.** There was no `ADMINS`, no
  `LOGGING` and no handler at all: an exception went to the container log and
  stopped there, which means the way Genmars would have learned a till was
  failing is a shop telephoning. `django.request` errors are mailed, and
  **`include_html=False` is load-bearing rather than tidy** — Django's HTML
  traceback embeds every local in every frame, which here means a decrypted
  Daraja passkey, a cashier's password, a customer's phone number or another
  business's basket, sent in plain text through a third-party relay. A test
  asserts there is no HTML alternative, and flipping the flag makes it fail
  with the secret visible in the report. It needs `RESEND_API_KEY`, which
  production has.

### Closed since this list was written

Left here briefly because a gap list nobody trusts is worse than no gap list.

- ~~No Content-Security-Policy~~ — shipped `Report-Only`, then promoted to
  enforcing, in the two halves the split host needs.
- ~~There is no dashboard~~ — branches, catalogue, stock, till, sales,
  refunds, reports, buying, staff and settings all exist, and the landing
  page no longer claims otherwise.
- ~~A branch manager's reports covered the whole organisation~~ — the
  permission half was right all along (`ReportViewSet` correctly demanded
  `reports.branch` rather than `reports.organisation`) and the queryset half
  was missing: nothing called `scoped_to_branch`, so asking for the overview
  with no branch named returned every branch's takings and the branch
  comparison handed over a table of them. Found while writing the buying
  reports, because the same mistake was available there. The comment in
  `ReportViewSet` asserted the confinement existed, which is worse than
  either half alone — code that reads as though it were handled. Both report
  modules now confine, and a third guard in `identity/tests/test_access.py`
  walks the URLconf for report viewsets, which the two existing guards skip
  because they filter on `TenantScoped` and a report is not.
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

## Being visible to Genmars

Until this existed, business-os appeared **nowhere in the ops dashboard**. It
talked to gen-portal for sign-on and nothing else, so the one application
Genmars sells to other businesses was the one the company could not see
running. `ops.genmars.co.ke/systems` has always been there; this was simply
never on it.

`Business_Platform/parent.py` POSTs a heartbeat to the system registry every
five minutes, driven by `deploy/genmars-business-heartbeat.timer`.

**The direction of trust is inward only.** CLAUDE.md: *"a child reports its
health and events, the parent reads them. There is deliberately no 'run
command' and no stored deployment credential."* This module POSTs upward and
accepts nothing downward — no endpoint gen-portal can call, no instruction it
will obey. If it ever grows one, that rule has gone and the registry's shape
with it.

**It reports a considered health, not "the process started."** `/healthz`
answers without touching the database on purpose, which is right for a
liveness probe and useless as a claim about the application. The heartbeat
checks the database, the migration state, and the things that fail silently.

Only two things make it **degraded**, and both silently break a promise
somebody has already been made:

| | |
|---|---|
| unapplied migrations | the code and the schema disagree; the symptom is a 500 on whichever page touches the new column |
| no mail configured | a cashier's password reset and a staff invitation both report success and send nothing |

A feature being switched off is **not** degradation — M-Pesa with no
`MPESA_CREDENTIAL_KEY` is reported in the detail and nowhere else. Amber that
is always on is green.

To turn it on: issue a `SystemKey` in ops under Settings → Engineering against
a System for this application, put it in `.env` as `GENMARS_SYSTEM_KEY`, and
install the timer. Absent, it reports nothing and nothing breaks — which is
the right behaviour for a laptop.

```bash
# What it would say, without sending anything:
DEBUG=1 virtual/bin/python manage.py report_health --dry-run
```

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

> ⚠ **The heartbeat timer is copied by that `cp` and deliberately not enabled
> here.** `genmars-business-heartbeat.timer` needs `GENMARS_SYSTEM_KEY` in
> `backend/.env` FIRST, because without it `report_health` exits non-zero and
> the unit's `OnFailure` mails through `genmars-alert@` — every five minutes,
> 288 times a day. GM-INC-2026-0001 is what that costs: alerts bounced, the
> provider suppressed the address, and thirty-one hours of real alerts were
> dropped in silence.
>
> The key is issued at **ops.genmars.co.ke/systems** → the system → *Reporting
> keys*. Founder only, shown exactly once, cannot be read back. Then:
>
> ```bash
> # add GENMARS_SYSTEM_KEY= to backend/.env — no sudo, it is mode 600 and
> # yours; `sudo -e` refuses it because the directory is user-writable
> docker compose up -d api    # `exec` runs in the EXISTING container, which
>                             # read env_file at creation — this is required
> docker compose exec -T api python manage.py report_health
> # must print "reported — up: …". Only then:
> sudo systemctl enable --now genmars-business-heartbeat.timer
> ```
>
> The full argument is in `deploy/genmars-business-heartbeat.service`.

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
virtual/bin/python manage.py test          # 501 tests
```
