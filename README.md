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
nothing from V2 or V3 has been started, and the table says so rather than
letting somebody discover it.

| §9 domain | Models | State |
|---|---|---|
| Organization | Organization, Membership, Role | **done** — Permission and Subscription are not built |
| Branches | Branch, Register, RegisterShift, StaffAssignment | **done** |
| Catalog | Product, Category, TaxRule | **done** — ProductPrice (price lists) is not built |
| Inventory | StockLevel, StockMovement, StockAdjustment, StockTransfer | **done** |
| Sales | Sale, SaleItem, Payment, Refund, Receipt | **done** |
| Procurement | Supplier, PurchaseOrder, GoodsReceipt | **not built** — V2 |

**V1 Core POS is complete**: organizations, branches, users/roles, products,
inventory, POS checkout, payments, customers, receipts and a basic
dashboard/reports.

**V2 is not started**: purchasing, suppliers, cash reconciliation at shift
close, returns workflow beyond the refund itself, advanced permissions,
notifications and multi-branch reporting beyond the branch comparison.
`RegisterShift` exists and `GET /sls/reports/register-status` already computes
the expected drawer figure a close would be reconciled against — closing one
is the piece that is missing.

**V3 is not started**: offline mode, M-Pesa, loyalty, accounting integrations,
e-commerce sync, advanced analytics, automated replenishment. §11 asks for
idempotency keys and transaction identifiers to be defined *before* production
rollout, and they are — sales and refunds both take one — so the offline
queue has something to synchronise against when it is built.

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

- **No Content-Security-Policy.** Deliberate: `business.caddy` says why —
  there was no markup to test one against. There is now, and the three pages
  in `identity/templates/` carry no inline `<style>` and no `style=""`
  attribute, so a policy can be written without `'unsafe-inline'`. Ship it
  `Report-Only` first.
- **There is no dashboard.** Signing in works and creates a platform account;
  there is then nothing to use it on. The landing page says so, and must keep
  saying so until it is untrue (Charter 04 §IV).
- **`OrganizationStaff.external_user_id`** is an orphan. It defaults to a
  fresh `uuid4()`, so it never equalled an id from anywhere, and the scoping
  that once joined through it has been replaced by `identity/scoping.py`. The
  field and its comment are still there and still misleading.
- **`StaffCredential.must_change_password`** is written but never enforced.

## Running the tests

```bash
cd backend
virtual/bin/python manage.py test          # 121 tests
```
