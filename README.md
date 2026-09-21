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

## Known gaps

These are written down rather than left to be rediscovered.

- **Nothing serves `/static/`.** There is no whitenoise in the requirements
  and no `file_server` in `deploy/business.caddy`, so `collectstatic` writes
  a directory nobody reads. The visible consequence is that `/admin/` renders
  unstyled, and it is why the three browser-facing templates in
  `identity/templates/` carry their CSS inline. Fixing it the way gen-portal
  does — bind the `staticfiles` directory out and let Caddy serve it — needs
  no new dependency (Charter 03 §I).
- **No Content-Security-Policy.** Deliberate: `business.caddy` says why, and
  it is bound up with the point above, since a CSP worth having will not want
  `'unsafe-inline'`.
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
virtual/bin/python manage.py test          # 65 tests
```
