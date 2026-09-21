"""
Standing up a business, which is the only way into an empty account.

A subscriber who has just signed in for the first time has no membership
anywhere. Every list is empty and stays empty. These cover the one endpoint
that changes that, and the ways it must not be abused.
"""

from __future__ import annotations

from rest_framework.test import APITestCase

from identity import services
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from identity.permissions import tenant_scope
from organisations.models import BusinessOrganization, OrganizationStaff

CREATE = "/org/organizations/"


def sign_in(client, account: PlatformAccount) -> None:
    session = client.session
    session["platform_account_id"] = account.pk
    session.save()


class TenantCreationTests(APITestCase):
    def setUp(self):
        self.account = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@example.co.ke", full_name="An Owner"
        )
        sign_in(self.client, self.account)

    def test_a_new_subscriber_starts_with_nothing(self):
        """The state this endpoint exists to resolve."""
        self.assertEqual(tenant_scope(self.account), [])
        self.assertEqual(self.client.get(CREATE).json(), [])

    def test_creating_a_business_makes_the_creator_its_owner(self):
        response = self.client.post(
            CREATE, {"name": "Kilimani Dental"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)

        organisation = BusinessOrganization.objects.get(name="Kilimani Dental")
        membership = TenantMembership.objects.get(
            account=self.account, organization=organisation
        )
        self.assertEqual(membership.role, TenantMembership.Role.OWNER)

    def test_it_is_visible_immediately_afterwards(self):
        """
        The half that makes it worth anything. An organisation saved without a
        membership is an orphan — invisible to its own creator and to everybody
        else, reachable only from the admin.
        """
        self.client.post(CREATE, {"name": "Kilimani Dental"}, format="json")

        listed = self.client.get(CREATE).json()
        self.assertEqual([row["name"] for row in listed], ["Kilimani Dental"])
        self.assertEqual(len(tenant_scope(self.account)), 1)

    def test_two_people_may_create_businesses_with_the_same_name(self):
        other = PlatformAccount.objects.create(
            genmars_account_id=2, email="other@example.co.ke"
        )
        self.client.post(CREATE, {"name": "Naivas"}, format="json")

        sign_in(self.client, other)
        second = self.client.post(CREATE, {"name": "Naivas"}, format="json")
        self.assertEqual(second.status_code, 201, second.content)

    def test_creating_one_grants_nothing_in_anybody_elses(self):
        stranger = PlatformAccount.objects.create(
            genmars_account_id=2, email="stranger@example.co.ke"
        )
        theirs = BusinessOrganization.objects.create(name="Somebody Else Ltd")
        TenantMembership.objects.create(account=stranger, organization=theirs)

        self.client.post(CREATE, {"name": "Mine"}, format="json")

        names = [row["name"] for row in self.client.get(CREATE).json()]
        self.assertEqual(names, ["Mine"])


class TenantCapTests(APITestCase):
    def setUp(self):
        self.account = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@example.co.ke"
        )
        sign_in(self.client, self.account)

    def test_the_cap_is_enforced_and_explains_itself(self):
        """
        Self-serve creation with no ceiling is a spam vector that costs nothing
        to open and something to clean up.
        """
        for i in range(services.MAX_TENANTS_PER_ACCOUNT):
            created = self.client.post(CREATE, {"name": f"Shop {i}"}, format="json")
            self.assertEqual(created.status_code, 201, created.content)

        refused = self.client.post(CREATE, {"name": "One Too Many"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("get in touch", refused.content.decode().lower())

    def test_a_refused_creation_leaves_no_orphan_behind(self):
        """
        The organisation and the membership are one transaction. An
        organisation nobody can reach is worse than a refusal, because nothing
        on any screen will ever show it again.
        """
        for i in range(services.MAX_TENANTS_PER_ACCOUNT):
            self.client.post(CREATE, {"name": f"Shop {i}"}, format="json")

        before = BusinessOrganization.objects.count()
        self.client.post(CREATE, {"name": "One Too Many"}, format="json")

        self.assertEqual(BusinessOrganization.objects.count(), before)
        self.assertFalse(
            BusinessOrganization.objects.filter(name="One Too Many").exists()
        )


class WhoMayCreateTests(APITestCase):
    def test_a_till_cannot_create_a_business(self):
        """
        A cashier signed in at one shop standing up a second business, which
        they would then own, is not a feature anybody asked for.
        """
        shop = BusinessOrganization.objects.create(name="Shop A")
        staff = OrganizationStaff.objects.create(
            organization=shop,
            full_name="Jane Cashier",
            email="jane@shop-a.co.ke",
            phone_number="+254700000001",
            address="Nairobi",
        )
        credential = StaffCredential(staff=staff, username="jane")
        credential.set_password("not-a-real-password")
        credential.save()

        _, token = services.open_staff_session(credential)

        before = BusinessOrganization.objects.count()
        response = self.client.post(
            CREATE,
            {"name": "A Shop Of My Own"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(BusinessOrganization.objects.count(), before)

    def test_a_stranger_cannot_create_a_business(self):
        response = self.client.post(CREATE, {"name": "Anything"}, format="json")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(BusinessOrganization.objects.count(), 0)

    def test_a_blocked_account_cannot_create_a_business(self):
        account = PlatformAccount.objects.create(
            genmars_account_id=9, email="blocked@example.co.ke", is_blocked=True
        )
        sign_in(self.client, account)

        response = self.client.post(CREATE, {"name": "Anything"}, format="json")
        self.assertIn(response.status_code, (400, 401, 403), response.content)
        self.assertEqual(BusinessOrganization.objects.count(), 0)
