"""
Admitting a second subscriber to a business.

══════════════════════════════════════════════════════════════════════════════
WHAT WAS MISSING, AND WHY IT WAS EASY TO MISS.

`create_tenant` was the only writer of a TenantMembership and it always granted
OWNER. So `admin` and `accountant` existed in the Role choices, had carefully
reasoned permission sets in identity/access.py, were covered by role tests —
and could not be given to anybody. Every subscriber was the sole owner of an
organisation they had made themselves, and a shop could not admit its own
bookkeeper.

Nothing failed. The roles simply had no door.
══════════════════════════════════════════════════════════════════════════════

An invitation hands somebody authority over another company's money, so the
tests that matter here are the refusals: inviting into a business that is not
yours, claiming an invitation that is not addressed to you, and leaving an
organisation with nobody who can administer it.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from identity import services, signon
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import (
    PlatformAccount,
    TenantInvitation,
    TenantMembership,
)
from organisations.models import BusinessOrganization


def an_account(number: int, email: str) -> PlatformAccount:
    return PlatformAccount.objects.create(genmars_account_id=number, email=email)


def owned(name: str, account: PlatformAccount) -> BusinessOrganization:
    org = BusinessOrganization.objects.create(name=name)
    TenantMembership.objects.create(
        account=account, organization=org, role=TenantMembership.Role.OWNER
    )
    return org


class InvitingTests(TestCase):
    def setUp(self):
        self.owner = an_account(5001, "owner@shop.co.ke")
        self.org = owned("Mwangi Stores", self.owner)
        self.sign_in(self.owner)

    def sign_in(self, account):
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

    def invite(self, email, role="admin", client=None):
        return (client or self.client).post(
            "/auth/invitations/",
            {"email": email, "role": role},
            content_type="application/json",
        )

    def test_an_owner_invites_an_admin(self):
        """The control. Every refusal below needs a path that works."""
        response = self.invite("books@shop.co.ke")
        self.assertEqual(response.status_code, 201, response.content)

        body = response.json()
        self.assertEqual(body["state"], "waiting")
        self.assertEqual(body["role"], "admin")
        # Nothing is granted yet.
        self.assertEqual(TenantMembership.objects.count(), 1)

    def test_the_organisation_cannot_be_named_in_the_request(self):
        """
        Blueprint §8, and the worst write this API could take: naming another
        shop would be a way to make yourself its owner by typing its id.
        """
        theirs = BusinessOrganization.objects.create(name="Somebody Else")

        response = self.client.post(
            "/auth/invitations/",
            {"email": "me@evil.example", "role": "owner", "organization": theirs.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        invitation = TenantInvitation.objects.get()
        self.assertEqual(
            invitation.organization_id,
            self.org.pk,
            "the caller's own business, never the one they asked for",
        )

    def test_inviting_somebody_already_in_the_business_is_refused(self):
        response = self.invite(self.owner.email)
        self.assertEqual(response.status_code, 400, response.content)

    def test_inviting_the_same_address_twice_is_refused(self):
        self.assertEqual(self.invite("books@shop.co.ke").status_code, 201)
        self.assertEqual(self.invite("books@shop.co.ke").status_code, 400)

    def test_a_stale_invitation_can_be_replaced(self):
        """
        Re-inviting somebody whose offer went cold is the ordinary case, not
        an edge one. The uniqueness constraint must not make it impossible.
        """
        self.assertEqual(self.invite("books@shop.co.ke").status_code, 201)
        TenantInvitation.objects.update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        self.assertEqual(self.invite("books@shop.co.ke").status_code, 201)

    def test_an_admin_may_not_invite(self):
        """
        MEMBERS_MANAGE is owner-only. An admin who could invite could invite
        themselves a second account and promote it — the permission that
        grants every other permission is the narrow one.
        """
        admin = an_account(5002, "admin@shop.co.ke")
        TenantMembership.objects.create(
            account=admin, organization=self.org, role=TenantMembership.Role.ADMIN
        )
        self.sign_in(admin)

        response = self.invite("friend@shop.co.ke")
        self.assertIn(response.status_code, (403, 404), response.content)
        self.assertEqual(TenantInvitation.objects.count(), 0)

    def test_another_shops_invitations_are_invisible(self):
        other_owner = an_account(5003, "other@shop.co.ke")
        other_org = owned("Other Shop", other_owner)
        TenantInvitation.objects.create(
            organization=other_org,
            email="theirs@shop.co.ke",
            role="admin",
            expires_at=timezone.now() + timedelta(days=7),
        )

        listed = self.client.get("/auth/invitations/").json()
        rows = listed if isinstance(listed, list) else listed["results"]
        self.assertEqual(rows, [])

    def test_withdrawing_an_offer(self):
        made = self.invite("books@shop.co.ke").json()
        response = self.client.post(
            f"/auth/invitations/{made['id']}/revoke/",
            {},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["state"], "withdrawn")


class ClaimingTests(TestCase):
    """
    The half that actually grants anything, and the half a link-based design
    would have got wrong.
    """

    PAYLOAD = {
        "account": {
            "id": 7001,
            "email": "books@shop.co.ke",
            "full_name": "A Bookkeeper",
            "is_staff": False,
            "staff_role": "",
            "email_verified": True,
        },
        "organisations": [],
    }

    def setUp(self):
        self.owner = an_account(6001, "owner@shop.co.ke")
        self.org = owned("Mwangi Stores", self.owner)

    def offer(self, email="books@shop.co.ke", role="accountant", **extra):
        fields = {
            "organization": self.org,
            "email": email,
            "role": role,
            "invited_by": self.owner,
            "expires_at": timezone.now() + timedelta(days=7),
        }
        # `extra` overrides rather than collides — the expiry and the revoked
        # stamp are exactly what these tests need to set.
        fields.update(extra)
        return TenantInvitation.objects.create(**fields)

    def sign_on(self, payload=None, client=None):
        client = client or self.client
        state = "state-for-the-claim"
        session = client.session
        session[signon.STATE_SESSION_KEY] = state
        session.save()
        with mock.patch.object(
            signon, "exchange_code", return_value=payload or self.PAYLOAD
        ):
            return client.get(
                reverse("sign-on-callback") + f"?code=good&state={state}",
                HTTP_ACCEPT="*/*",
            )

    def test_signing_on_with_the_invited_address_grants_the_membership(self):
        self.offer()
        response = self.sign_on()
        self.assertEqual(response.status_code, 200, response.content)

        membership = TenantMembership.objects.get(account__email="books@shop.co.ke")
        self.assertEqual(membership.organization_id, self.org.pk)
        self.assertEqual(membership.role, "accountant")
        self.assertEqual(membership.invited_by_id, self.owner.pk)

        # And the callback reports it, so the client lands on a dashboard
        # rather than on "create your business".
        self.assertEqual(len(response.json()["organisations"]), 1)

    def test_a_different_address_claims_nothing(self):
        """
        The whole security property. If this passed, typing somebody's email
        into your own invitation would be a way into their shop.
        """
        self.offer(email="books@shop.co.ke")

        stranger = dict(self.PAYLOAD)
        stranger["account"] = dict(self.PAYLOAD["account"])
        stranger["account"]["id"] = 7002
        stranger["account"]["email"] = "someone.else@elsewhere.example"

        self.sign_on(payload=stranger)
        self.assertFalse(
            TenantMembership.objects.filter(
                account__email="someone.else@elsewhere.example"
            ).exists()
        )
        self.assertTrue(
            TenantInvitation.objects.get().is_open, "the offer is still waiting"
        )

    def test_an_unverified_address_claims_nothing(self):
        """
        Matching on an address nobody proved they could read would let anyone
        join a shop by claiming its bookkeeper's email. accept_genmars_account
        refuses the token outright; this pins that the invite path inherits it.
        """
        self.offer()

        unverified = dict(self.PAYLOAD)
        unverified["account"] = dict(self.PAYLOAD["account"])
        unverified["account"]["email_verified"] = False

        response = self.sign_on(payload=unverified)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TenantMembership.objects.count(), 1, "only the owner")

    def test_an_expired_offer_claims_nothing(self):
        self.offer(expires_at=timezone.now() - timedelta(minutes=1))
        self.sign_on()
        self.assertEqual(TenantMembership.objects.count(), 1)

    def test_a_withdrawn_offer_claims_nothing(self):
        self.offer(revoked_at=timezone.now())
        self.sign_on()
        self.assertEqual(TenantMembership.objects.count(), 1)

    def test_an_offer_is_claimed_once_and_closes(self):
        """
        An invitation left open would re-fire on every sign-in and silently
        restore a role somebody had deliberately changed.
        """
        self.offer(role="accountant")
        self.sign_on()

        membership = TenantMembership.objects.get(account__email="books@shop.co.ke")
        membership.role = TenantMembership.Role.ADMIN
        membership.save()

        self.sign_on()
        membership.refresh_from_db()
        self.assertEqual(membership.role, "admin", "not restored to accountant")
        self.assertEqual(TenantInvitation.objects.get().state, "accepted")

    def test_case_does_not_decide_who_gets_in(self):
        """Nobody types an address the same way twice."""
        self.offer(email="Books@Shop.CO.KE")
        self.sign_on()
        self.assertTrue(
            TenantMembership.objects.filter(
                account__email="books@shop.co.ke"
            ).exists()
        )


class NeverWithoutAnOwnerTests(TestCase):
    """
    An organisation whose only owner has been removed is not "an organisation
    with no owner" — it is a business nobody can administer, invite into or
    recover, because the authority to recover it is the thing that was removed.
    """

    def setUp(self):
        self.owner = an_account(9001, "owner@shop.co.ke")
        self.org = owned("Mwangi Stores", self.owner)
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.owner.pk
        session.save()
        self.membership = TenantMembership.objects.get(account=self.owner)

    def test_the_last_owner_cannot_be_demoted(self):
        response = self.client.post(
            f"/auth/members/{self.membership.pk}/set-role/",
            {"role": "admin"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, "owner")

    def test_the_last_owner_cannot_be_removed(self):
        response = self.client.post(
            f"/auth/members/{self.membership.pk}/remove/",
            {},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertTrue(
            TenantMembership.objects.filter(pk=self.membership.pk).exists()
        )

    def test_with_a_second_owner_the_first_may_step_down(self):
        """The positive control — otherwise the guard could just be a wall."""
        second = an_account(9002, "partner@shop.co.ke")
        TenantMembership.objects.create(
            account=second, organization=self.org, role=TenantMembership.Role.OWNER
        )

        response = self.client.post(
            f"/auth/members/{self.membership.pk}/set-role/",
            {"role": "accountant"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, "accountant")

    def test_a_role_cannot_be_patched_directly(self):
        """
        A PATCH that set `role` would route around the last-owner check. The
        viewset is read-only for exactly that reason.
        """
        response = self.client.patch(
            f"/auth/members/{self.membership.pk}/",
            {"role": "accountant"},
            content_type="application/json",
        )
        self.assertIn(response.status_code, (403, 405), response.content)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, "owner")
