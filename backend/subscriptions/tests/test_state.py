"""
The state is derived from dates, so these tests are about dates.

Every one of them asks the same question at a different point on the calendar,
because the whole reason there is no `status` column is that a stored one
answers for the day a job last ran rather than for today.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from subscriptions import services
from subscriptions.models import State, Subscription, SubscriptionEvent

from .factories import a_plan, a_shop, a_subscriber


class StateTests(TestCase):
    def setUp(self):
        self.org, _ = a_shop()
        self.today = timezone.localdate()

    def a_subscription(self, **kwargs):
        kwargs.setdefault("started_on", self.today)
        return Subscription.objects.create(organization=self.org, **kwargs)

    def test_a_trial_that_has_not_ended_is_trialing(self):
        sub = self.a_subscription(trial_ends_on=self.today + timedelta(days=5))
        self.assertEqual(sub.state(), State.TRIALING)

    def test_the_last_day_of_a_trial_is_still_the_trial(self):
        """
        Inclusive, not exclusive. "Your trial ends on the 30th" and a shop
        locked out on the morning of the 30th is a support call, and the
        off-by-one is invisible until the day it happens to somebody.
        """
        sub = self.a_subscription(trial_ends_on=self.today)
        self.assertEqual(sub.state(), State.TRIALING)

    def test_a_paid_period_reads_as_active_even_during_a_trial(self):
        """Paid beats trialing when both cover today — the better of two
        true answers is the one to show."""
        sub = self.a_subscription(
            trial_ends_on=self.today + timedelta(days=5),
            paid_until=self.today + timedelta(days=2),
        )
        self.assertEqual(sub.state(), State.ACTIVE)

    def test_the_longer_of_trial_and_payment_is_what_covers_the_shop(self):
        """
        A tenant who pays early during a trial has both dates. Taking the
        payment alone would shorten the trial they were promised.
        """
        sub = self.a_subscription(
            trial_ends_on=self.today + timedelta(days=20),
            paid_until=self.today + timedelta(days=3),
        )
        self.assertEqual(sub.covered_until, self.today + timedelta(days=20))
        self.assertEqual(sub.state(self.today + timedelta(days=10)), State.TRIALING)

    def test_the_day_after_cover_ends_is_past_due_not_suspended(self):
        sub = self.a_subscription(
            paid_until=self.today - timedelta(days=1), grace_days=14
        )
        self.assertEqual(sub.state(), State.PAST_DUE)

    def test_the_last_day_of_grace_is_still_grace(self):
        sub = self.a_subscription(
            paid_until=self.today - timedelta(days=14), grace_days=14
        )
        self.assertEqual(sub.state(), State.PAST_DUE)

    def test_the_day_after_grace_is_suspended(self):
        sub = self.a_subscription(
            paid_until=self.today - timedelta(days=15), grace_days=14
        )
        self.assertEqual(sub.state(), State.SUSPENDED)

    def test_a_row_with_no_dates_at_all_entitles_nothing(self):
        """
        Not ACTIVE. Reading "no end date has passed" as good standing would
        make an empty row the most generous state in the system, and an empty
        row is what a half-finished import leaves behind.
        """
        self.assertEqual(self.a_subscription().state(), State.SUSPENDED)

    def test_a_cancellation_outranks_a_paid_period(self):
        sub = self.a_subscription(
            paid_until=self.today + timedelta(days=30),
            cancelled_on=self.today,
        )
        self.assertEqual(sub.state(), State.CANCELLED)

    def test_a_cancellation_dated_ahead_has_not_happened_yet(self):
        """
        `cancel` defaults to the end of the paid period, so this is the
        ordinary case and not an edge one: the shop keeps the month it bought.
        """
        sub = self.a_subscription(
            paid_until=self.today + timedelta(days=30),
            cancelled_on=self.today + timedelta(days=31),
        )
        self.assertEqual(sub.state(), State.ACTIVE)
        self.assertEqual(sub.state(self.today + timedelta(days=31)), State.CANCELLED)

    def test_the_state_moves_without_anything_running(self):
        """
        ── THE WHOLE REASON THERE IS NO STATUS COLUMN ─────────────────────
        One row, never written to again, read on four different days. A
        stored status would answer the same way every time until a job
        changed it, and the job is the thing that fails quietly.
        """
        sub = self.a_subscription(
            trial_ends_on=self.today + timedelta(days=10), grace_days=7
        )
        self.assertEqual(sub.state(self.today), State.TRIALING)
        self.assertEqual(sub.state(self.today + timedelta(days=10)), State.TRIALING)
        self.assertEqual(sub.state(self.today + timedelta(days=11)), State.PAST_DUE)
        self.assertEqual(sub.state(self.today + timedelta(days=17)), State.PAST_DUE)
        self.assertEqual(sub.state(self.today + timedelta(days=18)), State.SUSPENDED)


class LifecycleTests(TestCase):
    def setUp(self):
        self.org, _ = a_shop()
        self.owner = a_subscriber(self.org, number=901)
        self.today = timezone.localdate()

    def test_opening_a_trial_twice_does_not_hand_out_a_second_one(self):
        """
        Onboarding is a page a subscriber can revisit. A free month given
        away by a refresh is a bug nobody reports.
        """
        first = services.open_trial(self.org, days=30)
        first.paid_until = self.today + timedelta(days=90)
        first.save(update_fields=["paid_until"])

        again = services.open_trial(self.org, days=30)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(again.paid_until, self.today + timedelta(days=90))
        self.assertEqual(
            first.events.filter(kind=SubscriptionEvent.Kind.TRIAL_STARTED).count(), 1
        )

    def test_extending_backwards_is_refused(self):
        """
        Almost always a bug in whatever computed the date, and applying it
        silently takes back time a customer has paid for.
        """
        sub = services.open_trial(self.org)
        services.extend(sub, until=self.today + timedelta(days=30))
        with self.assertRaises(services.SubscriptionError):
            services.extend(sub, until=self.today + timedelta(days=10))
        sub.refresh_from_db()
        self.assertEqual(sub.paid_until, self.today + timedelta(days=30))

    def test_cancelling_keeps_the_period_already_paid_for(self):
        sub = services.open_trial(self.org)
        services.extend(sub, until=self.today + timedelta(days=30))
        services.cancel(sub, reason="Closing the shop", actor=self.owner)

        sub.refresh_from_db()
        self.assertEqual(sub.cancelled_on, self.today + timedelta(days=31))
        self.assertEqual(sub.state(), State.ACTIVE)
        self.assertEqual(sub.state(self.today + timedelta(days=31)), State.CANCELLED)

    def test_cancelling_an_already_lapsed_subscription_takes_effect_today(self):
        sub = services.open_trial(self.org, days=0)
        sub.trial_ends_on = self.today - timedelta(days=60)
        sub.save(update_fields=["trial_ends_on"])

        services.cancel(sub, actor=self.owner)
        sub.refresh_from_db()
        self.assertEqual(sub.cancelled_on, self.today)

    def test_reinstating_clears_the_field_and_keeps_the_event(self):
        """
        The log is append-only. A reversed decision that vanishes from the
        history turns a record of what happened into a record of the present.
        """
        sub = services.open_trial(self.org)
        services.cancel(sub, reason="Changed our minds", actor=self.owner)
        services.reinstate(sub, actor=self.owner)

        sub.refresh_from_db()
        self.assertIsNone(sub.cancelled_on)
        kinds = list(sub.events.values_list("kind", flat=True))
        self.assertIn(SubscriptionEvent.Kind.CANCELLED, kinds)
        self.assertIn(SubscriptionEvent.Kind.REINSTATED, kinds)

    def test_every_change_is_attributed_or_honestly_unattributed(self):
        sub = services.open_trial(self.org)
        plan = a_plan()
        services.choose_plan(sub, plan, actor=self.owner)

        trial = sub.events.get(kind=SubscriptionEvent.Kind.TRIAL_STARTED)
        chosen = sub.events.get(kind=SubscriptionEvent.Kind.PLAN_CHOSEN)
        # Opened by onboarding with no actor passed: null, not invented.
        self.assertIsNone(trial.actor_account)
        self.assertEqual(chosen.actor_account, self.owner)

    def test_a_withdrawn_plan_cannot_be_chosen_but_keeps_working(self):
        sub = services.open_trial(self.org)
        old = a_plan(code="legacy", is_offered=False)
        with self.assertRaises(services.SubscriptionError):
            services.choose_plan(sub, old)

        # Already on it: moving somebody off terms they agreed to is a
        # commercial decision, not a cleanup.
        sub.plan = old
        sub.save(update_fields=["plan"])
        services.choose_plan(sub, old)
