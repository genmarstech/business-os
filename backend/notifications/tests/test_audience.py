"""
Who may read a notification, and who must not.

═══════════════════════════════════════════════════════════════════════════════
A NOTIFICATION IS ADDRESSED TO A PERMISSION, SO THE AUDIENCE IS A SCOPING
QUESTION — AND A WRONG ANSWER HERE IS THE SAME CLASS OF BUG AS A LEAKY
QUERYSET.

Three things have to hold and each has its own test below:

  · tenant   another shop's notifications are not in the answer, at all
  · branch   a cashier at Westlands does not read Karen's short drawer, even
             though they hold the same permission at their own branch
  · grant    holding the permission is what admits somebody, not their role
             name and not being an employee of the business
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

from branches.models import Branches, staffAssignment
from django.test import TestCase

from identity import access
from identity import services as identity_services
from identity.authentication import StaffPrincipal
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from notifications import services
from notifications.models import Notification, NotificationRead
from organisations.models import BusinessOrganization, OrganizationStaff

PASSWORD = "till-password-not-real"


def a_shop(name, *, branches=("Main",)):
    org = BusinessOrganization.objects.create(name=name)
    made = [
        Branches.objects.create(
            organization=org,
            branch_name=f"{name} {b}",
            branch_location="Nairobi",
            branch_allocation="Ground floor",
            branch_manager="A Manager",
            is_active=True,
        )
        for b in branches
    ]
    return org, made


class AudienceBase(TestCase):
    def staff_at(self, org, branch, role, *, n, username):
        """A till principal holding `role` at one branch."""
        person = OrganizationStaff.objects.create(
            organization=org,
            full_name=f"Person {n}",
            email=f"p{n}@example.co.ke",
            phone_number=f"+2547{n:08d}",
            address="Nairobi",
            id_number=n,
        )
        staffAssignment.objects.create(
            staff_member=person, branch=branch, staff_assignment=role
        )
        credential = StaffCredential(staff=person, username=username)
        credential.set_password(PASSWORD)
        credential.save()
        session, _ = identity_services.open_staff_session(credential)
        return StaffPrincipal(session)

    def subscriber(self, org, *, n, role=TenantMembership.Role.OWNER):
        account = PlatformAccount.objects.create(
            genmars_account_id=n, email=f"owner{n}@example.co.ke"
        )
        TenantMembership.objects.create(account=account, organization=org, role=role)
        return account

    def notify(self, org, *, permission, branch=None, kind=None, subject="Something"):
        return services.raise_notification(
            kind=kind or Notification.Kind.SHIFT_CLOSED,
            organization_id=org.pk,
            permission=permission,
            branch_id=branch.pk if branch else None,
            subject=subject,
        )

    def subjects(self, principal):
        return sorted(n.subject for n in services.feed(principal))


class TenantIsolationTests(AudienceBase):
    def setUp(self):
        self.a, (self.a_main,) = a_shop("Shop A")
        self.b, (self.b_main,) = a_shop("Shop B")

    def test_a_subscriber_never_sees_another_shops_notifications(self):
        self.notify(self.a, permission=access.REPORTS_BRANCH, subject="Ours")
        self.notify(self.b, permission=access.REPORTS_BRANCH, subject="Theirs")

        owner = self.subscriber(self.a, n=1)
        self.assertEqual(self.subjects(owner), ["Ours"])

    def test_a_till_never_sees_another_shops_notifications(self):
        self.notify(self.a, permission=access.SALES_VIEW, branch=self.a_main,
                    subject="Ours")
        self.notify(self.b, permission=access.SALES_VIEW, branch=self.b_main,
                    subject="Theirs")

        cashier = self.staff_at(self.a, self.a_main, "CA", n=10000001, username="jm")
        self.assertEqual(self.subjects(cashier), ["Ours"])

    def test_the_unread_count_is_scoped_too(self):
        """
        ⚠ A count is as much of a leak as a list. "You have 14 unread" where
          eleven belong to another shop tells somebody how busy a competitor is.
        """
        for i in range(3):
            self.notify(self.b, permission=access.SALES_VIEW, subject=f"Theirs {i}")
        self.notify(self.a, permission=access.REPORTS_BRANCH, subject="Ours")

        owner = self.subscriber(self.a, n=1)
        self.assertEqual(services.unread_count(owner), 1)


class BranchConfinementTests(AudienceBase):
    def setUp(self):
        self.org, (self.westlands, self.karen) = a_shop(
            "Two Branches", branches=("Westlands", "Karen")
        )

    def test_a_cashier_does_not_read_another_branchs_notification(self):
        """
        Same permission, different branch. §5: a branch manager "should see
        only the data and actions permitted for that branch".
        """
        self.notify(self.org, permission=access.SALES_VIEW, branch=self.westlands,
                    subject="At Westlands")
        self.notify(self.org, permission=access.SALES_VIEW, branch=self.karen,
                    subject="At Karen")

        cashier = self.staff_at(
            self.org, self.westlands, "CA", n=10000001, username="jm"
        )
        self.assertEqual(self.subjects(cashier), ["At Westlands"])

    def test_one_person_with_two_hats_reads_each_branch_by_its_own_grant(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE CASE THE UNION WOULD GET WRONG.

        A cashier at Westlands and the manager at Karen. `reports.branch` comes
        with the manager's role and not the cashier's — so Karen's short drawer
        is theirs to read and Westlands' is not, and a filter built from
        `granted(principal)` with no branch would hand them both.
        ══════════════════════════════════════════════════════════════════════
        """
        person = OrganizationStaff.objects.create(
            organization=self.org,
            full_name="Two Hats",
            email="two@example.co.ke",
            phone_number="+254700000001",
            address="Nairobi",
            id_number=20000001,
        )
        staffAssignment.objects.create(
            staff_member=person, branch=self.westlands, staff_assignment="CA"
        )
        staffAssignment.objects.create(
            staff_member=person, branch=self.karen, staff_assignment="AM"
        )
        credential = StaffCredential(staff=person, username="twohats")
        credential.set_password(PASSWORD)
        credential.save()
        session, _ = identity_services.open_staff_session(credential)
        principal = StaffPrincipal(session)

        self.notify(self.org, permission=access.REPORTS_BRANCH,
                    branch=self.westlands, subject="Westlands takings")
        self.notify(self.org, permission=access.REPORTS_BRANCH,
                    branch=self.karen, subject="Karen takings")

        self.assertEqual(self.subjects(principal), ["Karen takings"])

    def test_an_organisation_wide_notification_reaches_a_branch_principal(self):
        """
        Null branch means the whole organisation, not "no branch" — the same
        convention as access.branch_scope, where the two readings are
        opposites.
        """
        self.notify(self.org, permission=access.SALES_VIEW, branch=None,
                    subject="For the business")

        cashier = self.staff_at(
            self.org, self.westlands, "CA", n=10000001, username="jm"
        )
        self.assertEqual(self.subjects(cashier), ["For the business"])

    def test_a_subscriber_reads_every_branch(self):
        """Organisation-wide authority by §2, so no branch narrows it."""
        self.notify(self.org, permission=access.REPORTS_BRANCH,
                    branch=self.westlands, subject="Westlands")
        self.notify(self.org, permission=access.REPORTS_BRANCH,
                    branch=self.karen, subject="Karen")

        owner = self.subscriber(self.org, n=1)
        self.assertEqual(self.subjects(owner), ["Karen", "Westlands"])


class ThePermissionIsWhatAdmitsThemTests(AudienceBase):
    def setUp(self):
        self.org, (self.branch,) = a_shop("One Branch")

    def test_a_cashier_does_not_read_what_needs_a_managers_permission(self):
        """
        A short drawer is addressed to `reports.branch`, which a cashier
        deliberately does not hold — the person who counted it should not be
        the only one who knows, and should not be the only one who could hide
        it either.
        """
        self.notify(self.org, permission=access.REPORTS_BRANCH, branch=self.branch,
                    subject="Till closed short")

        cashier = self.staff_at(self.org, self.branch, "CA", n=10000001, username="jm")
        self.assertEqual(self.subjects(cashier), [])

    def test_a_manager_does_read_it(self):
        """The control. "Refused" must not be able to mean "broken for all"."""
        self.notify(self.org, permission=access.REPORTS_BRANCH, branch=self.branch,
                    subject="Till closed short")

        manager = self.staff_at(self.org, self.branch, "AM", n=10000002, username="am")
        self.assertEqual(self.subjects(manager), ["Till closed short"])

    def test_an_accountant_does_not_read_an_approval_they_cannot_give(self):
        """
        `purchasing.approve` is a branch manager's and an owner's. An
        accountant reads the money and approves nothing, so an order waiting
        for approval is not theirs to be told about.
        """
        self.notify(self.org, permission=access.PURCHASING_APPROVE,
                    kind=Notification.Kind.ORDER_AWAITING, subject="Order 1 waiting")

        accountant = self.subscriber(
            self.org, n=1, role=TenantMembership.Role.ACCOUNTANT
        )
        self.assertEqual(self.subjects(accountant), [])

    def test_losing_an_assignment_takes_the_notification_away(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE REASON THE AUDIENCE IS NOT A RECIPIENT COLUMN.

        A row naming a person keeps notifying somebody after they are taken off
        the rota. Addressed to an authority and resolved at read time, it stops
        — on the next request, not at the next sign-in.
        ══════════════════════════════════════════════════════════════════════
        """
        self.notify(self.org, permission=access.REPORTS_BRANCH, branch=self.branch,
                    subject="Till closed short")
        manager = self.staff_at(self.org, self.branch, "AM", n=10000002, username="am")
        self.assertEqual(len(self.subjects(manager)), 1)

        staffAssignment.objects.filter(staff_member=manager.staff).update(
            is_active=False
        )
        self.assertEqual(self.subjects(manager), [])


class UnknownPermissionTests(AudienceBase):
    def test_raising_against_an_unknown_permission_is_an_error(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE ONE PLACE FAILING CLOSED IS WORSE THAN FAILING LOUDLY.

        `access.may` refuses an unknown permission name, which is right
        everywhere else: a typo removes a button and somebody complains. Here
        the same typo would write a notification addressed to a permission
        nobody holds — read by NOBODY, looking exactly like a feature that
        works, because the people who would notice are the people not being
        told.
        ══════════════════════════════════════════════════════════════════════
        """
        org, (branch,) = a_shop("Typo Shop")
        with self.assertRaises(services.NotificationError):
            services.raise_notification(
                kind=Notification.Kind.SHIFT_CLOSED,
                organization_id=org.pk,
                permission="reports.brnach",
                subject="Never read by anyone",
            )
        self.assertEqual(Notification.objects.count(), 0)


class ReadStateTests(AudienceBase):
    def setUp(self):
        self.org, (self.branch,) = a_shop("One Branch")
        self.owner = self.subscriber(self.org, n=1)
        self.first = self.notify(self.org, permission=access.REPORTS_BRANCH,
                                 subject="One")
        self.second = self.notify(self.org, permission=access.REPORTS_BRANCH,
                                  subject="Two")

    def test_reading_one_leaves_the_other_outstanding(self):
        """
        What a watermark could not express: dealt with that one, not this one.
        """
        services.mark_read(self.owner, ids=[self.first.pk])

        self.assertEqual(services.unread_count(self.owner), 1)
        self.assertEqual(self.subjects(self.owner), ["Two"])

    def test_marking_all_read_empties_the_bell(self):
        services.mark_read(self.owner)
        self.assertEqual(services.unread_count(self.owner), 0)
        self.assertEqual(self.subjects(self.owner), [])

    def test_reading_twice_is_not_two_rows(self):
        """
        ⚠ The unread count is a difference between two counts, so a duplicate
          read row would make it negative. Two tabs pressing "mark all read" is
          ordinary rather than exceptional.
        """
        services.mark_read(self.owner)
        services.mark_read(self.owner)
        self.assertEqual(NotificationRead.objects.count(), 2)
        self.assertEqual(services.unread_count(self.owner), 0)

    def test_read_state_is_per_person(self):
        other = self.subscriber(self.org, n=2)
        services.mark_read(self.owner)

        self.assertEqual(services.unread_count(self.owner), 0)
        self.assertEqual(services.unread_count(other), 2)

    def test_a_caller_cannot_mark_another_shops_notification_read(self):
        """
        ⚠ The ids come from a request. Without re-applying the audience filter
          this writes read rows against rows the caller cannot see — which also
          confirms which ids exist, the enumeration oracle the 404-not-403 rule
          elsewhere exists to close.
        """
        other_org, _ = a_shop("Somebody Else")
        theirs = self.notify(other_org, permission=access.REPORTS_BRANCH,
                             subject="Not yours")

        marked = services.mark_read(self.owner, ids=[theirs.pk])

        self.assertEqual(marked, 0)
        self.assertFalse(NotificationRead.objects.filter(notification=theirs).exists())

    def test_a_resolved_notification_leaves_the_feed_without_being_read(self):
        keyed = services.raise_notification(
            kind=Notification.Kind.ORDER_AWAITING,
            organization_id=self.org.pk,
            permission=access.PURCHASING_APPROVE,
            subject="Order 9 waiting",
            subject_key="purchaseorder:9",
        )
        owner_sees = self.subjects(self.owner)
        self.assertIn("Order 9 waiting", owner_sees)

        services.resolve(
            organization_id=self.org.pk,
            kind=Notification.Kind.ORDER_AWAITING,
            subject_key="purchaseorder:9",
        )

        self.assertNotIn("Order 9 waiting", self.subjects(self.owner))
        keyed.refresh_from_db()
        self.assertIsNotNone(keyed.resolved_at)

    def test_resolving_without_a_subject_key_is_refused(self):
        """
        ⚠ AN EMPTY KEY WOULD MATCH EVERY NOTIFICATION OF THAT KIND IN THE
          SHOP. `subject_key` is blank by default, so a caller that failed to
          build one would silently clear the whole feed of short drawers rather
          than resolving the one it meant. This test is why the check exists —
          it found it by doing exactly that.
        """
        with self.assertRaises(services.NotificationError):
            services.resolve(
                organization_id=self.org.pk,
                kind=Notification.Kind.SHIFT_CLOSED,
                subject_key="",
            )
        self.assertEqual(services.unread_count(self.owner), 2)
