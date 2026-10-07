# Notifications

Telling somebody something happened, without knowing who they are.

## A notification is addressed to an authority, not to a person

A row names a **permission** and a **scope**, and the audience is resolved from
live `identity.access` at read time.

```
Notification(permission="purchasing.approve", organization=4, branch=7)
    → everybody who holds purchasing.approve at branch 7, right now
```

The obvious model has a recipient column. It cannot work here:

- **There are two kinds of principal.** A subscriber is a `PlatformAccount`; a
  cashier is a `StaffCredential` over an `OrganizationStaff`, and the two
  credential stores must never meet. A recipient column would be two nullable
  foreign keys on every row and two cases in every query.
- **The right audience changes after the event.** "This order needs approving"
  is for whoever holds `purchasing.approve` — not the same set of people this
  week as last. A row naming a person keeps notifying somebody who left and
  never reaches the person promoted this morning, and nothing about it looks
  wrong. `test_losing_an_assignment_takes_the_notification_away` pins this.

`NotificationRead` is the one table that does name a principal, because "have
you read this" is the one question genuinely about a person rather than an
authority. Two nullable columns and a check constraint that exactly one is set.

> ⚠ `branch = NULL` means **the whole organisation**, not "no branch" — the
> same convention as `access.branch_scope`. The two readings are opposites, so
> one of them has to be written down.

## Events, not conditions — with one deliberate exception

Most of this is a **moment**: a payment settled, an order was submitted, a
drawer was counted short. It happened, it is read, it is done.

Low stock is a **state**, and conflating the two is how a notification system
becomes useless. Raising one per stock movement past the reorder level is a
notification for every sale of a popular item; raising one and leaving it is
"out of stock" sitting there for something restocked an hour ago.

So a stock row is raised on the **crossing**, deduplicated while unresolved,
and `resolved_at` is stamped when the quantity goes back above the level. A
resolved row leaves the feed and stays in the table, because "how long were we
out of sugar" is a question somebody asks later.

## What raises one, and from where

| event | raised in | addressed to |
|---|---|---|
| `stock.low` / `stock.out` | `sales`, `procurement`, `inventory` services | `inventory.view` |
| `payment.confirmed` / `payment.failed` | `payments.services._settle` | `sales.view` |
| `order.awaiting` | `procurement.services.submit_order` | `purchasing.approve` |
| `count.awaiting` | `inventory.services.record_count` | `inventory.count.close` |
| `shift.closed` / `shift.short` | `branches.services.close_shift` | `reports.branch` |

### Stock is written in three places and there is no choke point

```
sales/services.py        a sale takes stock off the shelf   ← the important one
procurement/services.py  a delivery puts it back
inventory/services.py    adjust(), for everything else
```

**The sale is the one that matters.** Stock runs low *because of selling*, so a
low-stock notification wired only to the manual adjustment screen would fire
for a correction and stay silent for the shop actually running out.
`test_raising.py` exercises all three through their real service functions,
which is the guard against a fourth writer being added without a fourth call.

### Two paths that are easy to forget

An order stops waiting when it is **approved or cancelled**; a count stops
waiting when it is **signed off or abandoned**. The happy path is the one people
hook, so the other would leave a row asking to be approved for ever. Both are
resolved, and both have a test.

## Three refusals worth knowing

**An unknown permission name raises.** `access.may` fails closed on a typo,
which is right everywhere else — a button disappears and somebody complains.
Here the same typo writes a notification addressed to a permission nobody
holds, read by **nobody**, looking exactly like a feature that works, because
the people who would notice are the people not being told. In the one place
failing closed is invisible, fail loudly.

**`resolve()` refuses an empty `subject_key`.** It is blank by default and most
kinds never set one, so an empty string matches *every* unresolved notification
of that kind in the organisation — one caller that failed to build a key would
silently clear a shop's whole feed of short drawers. Found by a test that did
exactly that by accident.

**There is no POST that creates one.** A notification is a statement by the
system that something happened. An endpoint that let a client assert one would
let anybody holding a token manufacture "M-Pesa payment confirmed" into a
manager's feed, and the entries being true is the whole value of the thing.

## It can never fail a sale

Every raise goes through `services._safely`, which swallows and logs. These are
called inside the transactions that take payments and close drawers, and an
exception escaping one would roll that back — so a shop would be unable to
trade because of a bug in the code that tells somebody about trading.

> ⚠ The cost is that a broken notification is **invisible to the person who
> should have received it**. The log line is the only evidence, so it names the
> event rather than saying "notification failed". Every test in this app
> asserts a row EXISTS for that reason: the 662-test suite passed the moment
> the hooks were added, which proved only that nothing broke.

`test_a_sale_completes_even_when_raising_blows_up` breaks the raise on purpose
and asserts the sale still completes.

## What a notification must never carry

- **A customer's phone number or M-Pesa receipt.** `ActivityLog.detail` in
  gen-portal carries the same prohibition. The amount and the outcome are what
  a manager needs; a number in a feed is that number in every manager's browser
  and in whatever they screenshot it into.
- **What a stock count found.** The count is **blind** — show somebody the
  expected figure and they count to it, and the stock take stops detecting
  anything. `count.awaiting` says a count is ready and nothing about the
  variance, and `test_it_says_nothing_about_what_the_count_found` asserts the
  words.
- **The `permission` field.** It is the audience rule, and it describes other
  people's access as much as the reader's. The server has already decided they
  may see the row; how it decided is not part of the answer.

## Why it is polled and not pushed

```
GET  /ntf/            the feed, newest first, with the unread count beside it
GET  /ntf/unread      one integer, for the bell
POST /ntf/read        {"ids": [...]}, or {} for everything outstanding
```

Server-Sent Events and WebSockets are both refused by how this is served, not
by taste: **gunicorn runs three synchronous workers** (`backend/Dockerfile`),
and a held-open connection occupies one for its whole life. A shop with three
tills and a manager's laptop would consume every worker and the API would stop
answering — the till mid-sale included.

Push would mean an async worker class and, across processes, something to fan
out between them. That is a dependency and an operational surface, and Charter
03 §I says one enters the stack only when what is already there cannot do the
job. A count at most thirty seconds stale does the job, and `/ntf/unread` is
built to be asked for that often: a `COUNT` over an indexed filter, no
serialisation, no joins. The browser also stops polling entirely while the tab
is hidden.

## The till has no bell, and that is deliberate

`/till` is a terminal rather than a dashboard — it has no sidebar for the same
reason. A cashier's two relevant notifications are already in front of them: a
payment's outcome is on the screen they are watching, and they cannot order
stock.

The API is tier-agnostic, so a staff principal's bearer token reads `/ntf/`
correctly today. When the purchasing officer, finance clerk and branch auditor
get the till workspaces they are still missing, the feed is already there for
them.
