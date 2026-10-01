"""
Genmars recording that a customer has paid.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS MATTERS MORE THAN IT LOOKS.

`services.extend` existed and nothing called it. A customer could pay by bank
transfer, by M-Pesa to the company till, in cash — and Genmars had no way to
record it, so the trial lapsed and the subscription suspended regardless. The
product was not chargeable, not because the money could not be taken but
because its arrival could not be written down.

The admin meanwhile let every field be typed into directly, so `paid_until`
could be moved eighteen months out with nothing in the append-only log saying
who did it or why. The one surface Genmars actually uses was the one that
bypassed the rule the API enforces.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from subscriptions import services
from subscriptions.admin import SubscriptionAdmin
from subscriptions.models import State, Subscription, SubscriptionEvent

from .factories import a_plan, a_shop


class SubscriptionAdminTests(TestCase):
    def setUp(self):
        self.admin = SubscriptionAdmin(Subscription, AdminSite())
        self.org, _ = a_shop()
        self.subscription = services.open_trial(self.org)
        self.today = timezone.localdate()
        self.operator = User.objects.create_superuser(
            username="root", email="r@genmars.co.ke", password="x"
        )

    def post(self, action, data):
        request = RequestFactory().post("/admin/", {**data, "apply": "1"})
        request.user = self.operator
        # The messages framework needs somewhere to put things.
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(request, "session", {})
        setattr(request, "_messages", FallbackStorage(request))
        return request

    # ── the gap this closes ─────────────────────────────────────────────────

    def test_a_payment_can_be_recorded_at_all(self):
        """
        ══════════════════════════════════════════════════════════════════
        Nothing called services.extend before this. A paid customer's
        subscription lapsed anyway.
        ══════════════════════════════════════════════════════════════════
        """
        until = self.today + timedelta(days=30)
        request = self.post("record_payment", {"until": until.isoformat(),
                                               "note": "bank transfer"})
        self.admin.record_payment(
            request, Subscription.objects.filter(pk=self.subscription.pk)
        )

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.paid_until, until)
        self.assertEqual(self.subscription.state(), State.ACTIVE)

    def test_recording_one_writes_the_event_beside_it(self):
        """
        The whole reason the admin routes through services rather than
        saving the field: a change to what a customer owes with no record
        of who decided it is not a record.
        """
        until = self.today + timedelta(days=30)
        request = self.post("record_payment", {"until": until.isoformat(),
                                               "note": "paid in cash"})
        self.admin.record_payment(
            request, Subscription.objects.filter(pk=self.subscription.pk)
        )

        event = self.subscription.events.get(kind=SubscriptionEvent.Kind.EXTENDED)
        self.assertEqual(event.note, "paid in cash")
        self.assertEqual(event.detail["until"], until.isoformat())

    def test_a_backwards_date_is_refused_rather_than_applied(self):
        """
        Almost always a bug in whoever worked the date out, and applying it
        takes back time a customer has paid for.
        """
        services.extend(self.subscription, until=self.today + timedelta(days=30))

        request = self.post(
            "record_payment",
            {"until": (self.today + timedelta(days=5)).isoformat(), "note": ""},
        )
        self.admin.record_payment(
            request, Subscription.objects.filter(pk=self.subscription.pk)
        )

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.paid_until, self.today + timedelta(days=30))

    def test_a_refusal_for_one_does_not_stop_the_others(self):
        """
        Acting on a page of subscriptions at once must not be all-or-
        nothing: one bad row would silently drop nineteen good ones.
        """
        other_org, _ = a_shop("Shop B")
        other = services.open_trial(other_org)
        services.extend(other, until=self.today + timedelta(days=90))

        until = self.today + timedelta(days=30)
        request = self.post("record_payment", {"until": until.isoformat(),
                                               "note": ""})
        self.admin.record_payment(request, Subscription.objects.all())

        self.subscription.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.subscription.paid_until, until)       # applied
        self.assertEqual(other.paid_until, self.today + timedelta(days=90))  # refused

    def test_moving_to_a_plan_applies_its_ceilings(self):
        """
        Choosing a plan is what makes a limit real — `entitlement.may_add`
        has nothing to enforce until one is set.
        """
        from subscriptions import entitlement

        plan = a_plan(code="starter", name="Starter", branch_limit=1)
        request = self.post("move_to_plan", {"plan": str(plan.pk)})
        self.admin.move_to_plan(
            request, Subscription.objects.filter(pk=self.subscription.pk)
        )

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan, plan)
        verdict = entitlement.may_add(self.org.pk, entitlement.BRANCH)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "plan_limit")

    def test_cancelling_keeps_what_was_paid_for(self):
        services.extend(self.subscription, until=self.today + timedelta(days=30))
        request = self.post("cancel", {})
        self.admin.cancel(request, Subscription.objects.filter(pk=self.subscription.pk))

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.cancelled_on, self.today + timedelta(days=31))
        self.assertEqual(self.subscription.state(), State.ACTIVE)

    def test_reinstating_clears_it_and_keeps_the_event(self):
        services.cancel(self.subscription)
        request = self.post("reinstate", {})
        self.admin.reinstate(
            request, Subscription.objects.filter(pk=self.subscription.pk)
        )

        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.cancelled_on)
        kinds = set(self.subscription.events.values_list("kind", flat=True))
        self.assertIn(SubscriptionEvent.Kind.CANCELLED, kinds)
        self.assertIn(SubscriptionEvent.Kind.REINSTATED, kinds)

    # ── the hole it closes ──────────────────────────────────────────────────

    def test_no_field_can_be_typed_into_directly(self):
        """
        ══════════════════════════════════════════════════════════════════
        The admin used to allow exactly what the API forbids: every field
        editable, so paid_until could be moved by typing in a box with the
        append-only log saying nothing happened.

        Every field that carries a commercial fact is read-only, so the
        only way to move one is an action that writes an event.
        ══════════════════════════════════════════════════════════════════
        """
        for field in ("paid_until", "trial_ends_on", "plan", "cancelled_on",
                      "grace_days", "organization"):
            self.assertIn(field, self.admin.readonly_fields)

    def test_a_subscription_cannot_be_made_or_deleted_here(self):
        """
        One is opened by open_trial inside the transaction that creates the
        business; deleting one deletes the record of what a customer agreed
        to and paid.
        """
        self.assertFalse(self.admin.has_add_permission(None))
        self.assertFalse(self.admin.has_delete_permission(None))

    def test_the_event_log_is_not_writable_from_the_admin_either(self):
        from subscriptions.admin import SubscriptionEventAdmin

        log = SubscriptionEventAdmin(SubscriptionEvent, AdminSite())
        self.assertFalse(log.has_add_permission(None))
        self.assertFalse(log.has_change_permission(None))
        self.assertFalse(log.has_delete_permission(None))
