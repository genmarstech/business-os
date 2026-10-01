"""
The subscription endpoints, and the 402 a growth action gets when the
subscription does not cover it.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import TenantMembership
from subscriptions import services
from subscriptions.models import Plan, State, Subscription

from .factories import (
    a_plan,
    a_register,
    a_shop,
    a_staff,
    a_subscriber,
    a_till,
    assign,
)


class Base(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop("Shop A")
        self.subscription = services.open_trial(self.org)

    def sign_in_owner(self, number=90):
        account = a_subscriber(self.org, TenantMembership.Role.OWNER, number=number)
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()
        return account

    def post(self, path, payload, **extra):
        return self.client.post(
            path, json.dumps(payload), content_type="application/json", **extra
        )

    def suspend(self):
        self.subscription.trial_ends_on = timezone.localdate() - timedelta(days=365)
        self.subscription.save(update_fields=["trial_ends_on"])


class GrowthRefusalTests(Base):
    def test_an_owner_can_add_a_branch_while_the_trial_runs(self):
        """The control. Every refusal below needs this to pass first."""
        self.sign_in_owner()
        response = self.post(
            "/brn/branch/",
            {
                "organization_id": self.org.pk,
                "branch_name": "Karen",
                "branch_location": "Nairobi",
                "branch_allocation": "Shop 9",
                "branch_manager": "K Manager",
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_suspended_shop_is_refused_with_402_not_403(self):
        """
        ── 402, AND THE DIFFERENCE MATTERS ────────────────────────────────
        403 says "you are not allowed to do this", which is a statement
        about authority and is wrong: an owner holds every permission there
        is. It sends them to look at roles, which is the one place the
        answer is not.
        """
        self.sign_in_owner()
        self.suspend()
        response = self.post(
            "/brn/branch/",
            {
                "organization_id": self.org.pk,
                "branch_name": "Karen",
                "branch_location": "Nairobi",
                "branch_allocation": "Shop 9",
                "branch_manager": "K Manager",
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 402, response.content)
        body = response.json()
        self.assertEqual(body["reason"], "subscription_inactive")
        self.assertTrue(body["selling_continues"])

    def test_a_plan_limit_refuses_a_second_till(self):
        self.sign_in_owner()
        self.subscription.plan = a_plan(code="starter", name="Starter", register_limit=1)
        self.subscription.save(update_fields=["plan"])
        a_register(self.branch)

        response = self.post(
            "/brn/register/",
            {"branch_id": self.branch.pk, "name": "Till 2", "register_number": "T2"},
        )
        self.assertEqual(response.status_code, 402, response.content)
        self.assertEqual(response.json()["reason"], "plan_limit")

    def test_moving_somebody_between_branches_is_not_growth(self):
        """
        An assignment create deactivates whatever that person held before,
        so it is a MOVE. Refusing it at the limit would strand a shop merely
        rearranging staff it already pays for.
        """
        self.sign_in_owner()
        west = self.branch
        from branches.models import Branches

        karen = Branches.objects.create(
            organization=self.org, branch_name="Karen", branch_location="Nairobi",
            branch_allocation="Shop 9", branch_manager="K", is_active=True,
        )
        grace = a_staff(
            self.org, name="Grace", email="grace@a.co.ke", id_number=4001
        )
        assign(grace, west)

        self.subscription.plan = a_plan(code="starter", staff_limit=1)
        self.subscription.save(update_fields=["plan"])

        response = self.post(
            "/brn/staff-assignments/",
            {"staff_member": grace.pk, "branch_id": karen.pk, "staff_assignment": "CA"},
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_second_person_at_the_limit_is_refused(self):
        """The control for the test above — the move must not be passing
        because the limit never fires."""
        self.sign_in_owner()
        grace = a_staff(self.org, name="Grace", email="g@a.co.ke", id_number=4001)
        assign(grace, self.branch)
        sam = a_staff(self.org, name="Sam", email="s@a.co.ke", id_number=4002)

        self.subscription.plan = a_plan(code="starter", staff_limit=1)
        self.subscription.save(update_fields=["plan"])

        response = self.post(
            "/brn/staff-assignments/",
            {"staff_member": sam.pk, "branch_id": self.branch.pk,
             "staff_assignment": "CA"},
        )
        self.assertEqual(response.status_code, 402, response.content)


class SubscriptionApiTests(Base):
    def test_an_owner_reads_their_own_subscription(self):
        self.sign_in_owner()
        response = self.client.get("/sub/subscription/")
        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()[0]
        self.assertEqual(row["state"], State.TRIALING)

    def test_another_tenants_subscription_is_not_visible(self):
        other, _ = a_shop("Shop B")
        other_sub = services.open_trial(other)
        self.sign_in_owner()

        self.assertEqual(len(self.client.get("/sub/subscription/").json()), 1)
        # 404, not 403 — a 403 confirms the row exists.
        self.assertEqual(
            self.client.get(f"/sub/subscription/{other_sub.pk}/").status_code, 404
        )

    def test_the_subscription_cannot_be_patched(self):
        """
        A PATCH that could set `paid_until` would be a payment with no money
        behind it. Every field is read-only and the viewset has no update
        route at all.
        """
        self.sign_in_owner()
        response = self.client.patch(
            f"/sub/subscription/{self.subscription.pk}/",
            json.dumps({"paid_until": "2099-01-01"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 405, response.content)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.paid_until)

    def test_a_cashier_cannot_read_the_subscription(self):
        """What the business pays is the owner's business, not a till's."""
        jane = a_staff(self.org, name="Jane", email="jane@a.co.ke", id_number=5001)
        assign(jane, self.branch)
        _, token = identity_services.open_staff_session(a_till(jane, "jane"))
        response = self.client.get(
            "/sub/subscription/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 403)

    def test_a_cashier_is_still_told_the_state(self):
        """
        A shop whose staff are the last to know is one where the owner finds
        out from somebody asking why a button is missing. The state travels;
        the price does not.
        """
        jane = a_staff(self.org, name="Jane", email="jane@a.co.ke", id_number=5001)
        assign(jane, self.branch)
        _, token = identity_services.open_staff_session(a_till(jane, "jane"))
        body = self.client.get(
            "/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}"
        ).json()

        self.assertEqual(body["subscription"]["state"], State.TRIALING)
        self.assertTrue(body["subscription"]["selling_continues"])
        self.assertNotIn("monthly_price", json.dumps(body["subscription"]))

    def test_cancelling_is_the_customers_own_decision_to_make(self):
        """A product that can only be left by emailing somebody traps people."""
        self.sign_in_owner()
        services.extend(
            self.subscription, until=timezone.localdate() + timedelta(days=30)
        )
        response = self.post(
            f"/sub/subscription/{self.subscription.pk}/cancel/",
            {"reason": "Closing the shop"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.cancelled_on)
        # And they keep what they paid for.
        self.assertEqual(self.subscription.state(), State.ACTIVE)

    def test_the_history_is_readable_and_not_writable(self):
        self.sign_in_owner()
        response = self.client.get(
            f"/sub/subscription/{self.subscription.pk}/history/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["kind"], "trial_started")
        # Null actor reads as Genmars, not as blank.
        self.assertEqual(response.json()[0]["actor_name"], "Genmars")


class PlanApiTests(Base):
    def test_plans_are_readable_by_any_signed_in_caller(self):
        a_plan(code="standard", name="Standard")
        self.sign_in_owner()
        response = self.client.get("/sub/plans/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["code"] for p in response.json()], ["standard"])

    def test_a_withdrawn_plan_is_not_offered(self):
        a_plan(code="legacy", is_offered=False)
        self.sign_in_owner()
        self.assertEqual(self.client.get("/sub/plans/").json(), [])

    def test_there_is_no_way_to_create_a_plan(self):
        """
        An API that let a tenant create a plan would let them create one
        with no limits.
        """
        self.sign_in_owner()
        response = self.post("/sub/plans/", {"code": "free", "name": "Free"})
        self.assertEqual(response.status_code, 405, response.content)
        self.assertEqual(Plan.objects.count(), 0)

    def test_plans_refuse_an_anonymous_caller(self):
        self.assertIn(self.client.get("/sub/plans/").status_code, (401, 403))


class OnboardingTests(TestCase):
    def test_creating_a_business_opens_a_trial_in_the_same_breath(self):
        """
        Not on a later screen. A tenant with no subscription row is one
        `state_of` has to guess about, and leaving it to a second step makes
        the guess the normal case.
        """
        from identity.models import PlatformAccount

        account = PlatformAccount.objects.create(
            genmars_account_id=777, email="new@owner.co.ke", full_name="New Owner"
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

        response = self.client.post(
            "/org/organizations/",
            json.dumps({"name": "Brand New Shop"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        subscription = Subscription.objects.get(
            organization__name="Brand New Shop"
        )
        self.assertEqual(subscription.state(), State.TRIALING)
        self.assertEqual(subscription.events.count(), 1)
