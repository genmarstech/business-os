"""
The properties that matter, written the way an attacker would probe them.

Every test here is one where a bug is a security incident rather than a
cosmetic defect: one shop reading another's data, a cashier's credential
working somewhere it should not, or an error message that reveals who exists.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from identity import services
from identity.authentication import SUBSCRIBER_SESSION_KEY, StaffPrincipal
from identity.models import (
    PlatformAccount,
    StaffCredential,
    StaffSession,
    TenantMembership,
)
from identity.permissions import scoped, tenant_scope
from organisations.models import BusinessOrganization, OrganizationStaff

PASSWORD = "till-password-not-real"


def make_org(name: str) -> BusinessOrganization:
    return BusinessOrganization.objects.create(name=name)


def make_staff(org, *, full_name, email, phone, id_number) -> OrganizationStaff:
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=full_name,
        email=email,
        phone_number=phone,
        address="Nairobi",
        id_number=id_number,
    )


def make_credential(staff, username, password=PASSWORD, **kwargs) -> StaffCredential:
    credential = StaffCredential(staff=staff, username=username, **kwargs)
    credential.set_password(password)
    credential.save()
    return credential


# ── the subscriber tier: Genmars identity, borrowed ─────────────────────────


class SubscriberIdentityTests(TestCase):
    def payload(self, **overrides):
        account = {
            "id": 12,
            "email": "owner@kilimani.co.ke",
            "full_name": "A Owner",
            "is_staff": True,
            "staff_role": "delivery",
            "email_verified": True,
        }
        account.update(overrides)
        return {
            "account": account,
            "organisations": [{"id": 3, "name": "Somebody Else Ltd"}],
        }

    def test_a_genmars_account_becomes_a_local_row(self):
        account = services.accept_genmars_account(self.payload())
        self.assertEqual(account.genmars_account_id, 12)
        self.assertEqual(account.email, "owner@kilimani.co.ke")

    def test_the_local_row_can_never_hold_a_credential(self):
        """
        The rule the whole two-tier design rests on. If PlatformAccount ever
        grows a password field, a subscriber has a second identity and the
        sibling rule in CLAUDE.md is broken.
        """
        fields = {f.name for f in PlatformAccount._meta.get_fields()}
        for forbidden in ("password", "password_hash", "secret", "token"):
            self.assertNotIn(forbidden, fields)

    def test_is_staff_from_the_token_grants_nothing(self):
        """
        `is_staff: True` means "works at Genmars". Inside a customer's shop it
        must mean nothing at all — otherwise every Genmars employee holds the
        till of every customer.
        """
        account = services.accept_genmars_account(self.payload(is_staff=True))
        self.assertEqual(tenant_scope(account), [])
        self.assertEqual(list(services.tenants_for(account)), [])

    def test_the_organisations_array_grants_nothing(self):
        """
        It is an onboarding hint about GENMARS memberships, not authority here.
        A tenant is entered because a TenantMembership row exists.
        """
        account = services.accept_genmars_account(self.payload())
        self.assertEqual(tenant_scope(account), [])

        org = make_org("Kilimani")
        TenantMembership.objects.create(account=account, organization=org)
        self.assertEqual(tenant_scope(account), [org.pk])

    def test_an_unverified_address_is_refused(self):
        with self.assertRaises(services.AuthError):
            services.accept_genmars_account(self.payload(email_verified=False))

    def test_a_blocked_account_is_refused(self):
        services.accept_genmars_account(self.payload())
        PlatformAccount.objects.filter(genmars_account_id=12).update(is_blocked=True)
        with self.assertRaises(services.AuthError):
            services.accept_genmars_account(self.payload())


# ── the operational tier: tenant-local, and locked to one shop ──────────────


class StaffAuthenticationTests(TestCase):
    def setUp(self):
        self.shop = make_org("Shop A")
        self.other = make_org("Shop B")
        self.staff = make_staff(
            self.shop,
            full_name="Jane Cashier",
            email="jane@shop-a.co.ke",
            phone="+254700000001",
            id_number=1001,
        )
        self.credential = make_credential(self.staff, "jane")

    def test_a_cashier_signs_in_to_their_own_shop(self):
        got = services.authenticate_staff(
            organization_id=self.shop.pk, username="jane", password=PASSWORD
        )
        self.assertEqual(got.pk, self.credential.pk)

    def test_the_same_credential_does_not_work_at_another_shop(self):
        """
        The organisation is part of the credential. Without this, a cashier's
        password is a password for the whole platform.
        """
        with self.assertRaises(services.AuthError):
            services.authenticate_staff(
                organization_id=self.other.pk, username="jane", password=PASSWORD
            )

    def test_two_shops_may_each_employ_a_jane(self):
        """
        Usernames are unique per tenant. Global uniqueness would be wrong, and
        would leak: the error would reveal that a name is taken in some other
        shop the caller cannot see.
        """
        other_staff = make_staff(
            self.other,
            full_name="Jane Other",
            email="jane@shop-b.co.ke",
            phone="+254700000002",
            id_number=1002,
        )
        make_credential(other_staff, "jane", password="a-different-password")

        a = services.authenticate_staff(
            organization_id=self.shop.pk, username="jane", password=PASSWORD
        )
        b = services.authenticate_staff(
            organization_id=self.other.pk,
            username="jane",
            password="a-different-password",
        )
        self.assertNotEqual(a.pk, b.pk)

    def test_five_wrong_passwords_lock_the_till(self):
        for _ in range(5):
            with self.assertRaises(services.AuthError):
                services.authenticate_staff(
                    organization_id=self.shop.pk, username="jane", password="wrong"
                )
        self.credential.refresh_from_db()
        self.assertTrue(self.credential.is_locked)

        # And the correct password is refused while it holds.
        with self.assertRaises(services.AuthError):
            services.authenticate_staff(
                organization_id=self.shop.pk, username="jane", password=PASSWORD
            )

    def test_every_refusal_reads_the_same(self):
        """
        Unknown username, wrong password, locked, deactivated — one message. A
        till stands in a public shop; the person at it must not be able to
        learn which usernames exist by watching the error change.
        """
        messages = set()

        def capture(**kwargs):
            try:
                services.authenticate_staff(**kwargs)
            except services.AuthError as error:
                messages.add(error.safe_message)

        capture(organization_id=self.shop.pk, username="nobody", password=PASSWORD)
        capture(organization_id=self.shop.pk, username="jane", password="wrong")

        self.credential.is_active = False
        self.credential.save()
        capture(organization_id=self.shop.pk, username="jane", password=PASSWORD)

        self.assertEqual(len(messages), 1, messages)

    def test_the_password_is_hashed_not_stored(self):
        self.assertNotIn(PASSWORD, self.credential.password)
        self.assertIn("$", self.credential.password)


class StaffSessionTests(TestCase):
    def setUp(self):
        self.shop = make_org("Shop A")
        staff = make_staff(
            self.shop,
            full_name="Jane Cashier",
            email="jane@shop-a.co.ke",
            phone="+254700000001",
            id_number=1001,
        )
        self.credential = make_credential(staff, "jane")

    def test_a_token_resolves_to_its_session(self):
        session, token = services.open_staff_session(self.credential)
        self.assertEqual(services.resolve_staff_session(token).pk, session.pk)

    def test_the_token_is_not_recoverable_from_the_database(self):
        _, token = services.open_staff_session(self.credential)
        stored = StaffSession.objects.get()
        self.assertNotIn(token, stored.token_hash)
        self.assertNotEqual(stored.token_hash, token)

    def test_an_expired_session_is_refused(self):
        session, token = services.open_staff_session(self.credential)
        session.expires_at = timezone.now() - timedelta(minutes=1)
        session.save()
        self.assertIsNone(services.resolve_staff_session(token))

    def test_signing_out_ends_it(self):
        session, token = services.open_staff_session(self.credential)
        services.close_staff_session(session)
        self.assertIsNone(services.resolve_staff_session(token))

    def test_deactivating_somebody_ends_the_shift_they_are_already_in(self):
        """
        Checked on every request, not only at the door. A shop sacking a
        cashier at 9pm needs the till to stop working at 9pm.
        """
        _, token = services.open_staff_session(self.credential)
        self.assertIsNotNone(services.resolve_staff_session(token))

        self.credential.is_active = False
        self.credential.save()
        self.assertIsNone(services.resolve_staff_session(token))

    def test_a_made_up_token_resolves_to_nothing(self):
        self.assertIsNone(services.resolve_staff_session("gbp_not-a-real-token"))
        self.assertIsNone(services.resolve_staff_session(""))


# ── isolation ───────────────────────────────────────────────────────────────


class TenantScopeTests(TestCase):
    def setUp(self):
        self.a = make_org("Shop A")
        self.b = make_org("Shop B")

    def test_a_till_sees_exactly_one_shop(self):
        staff = make_staff(
            self.a,
            full_name="Jane Cashier",
            email="jane@a.co.ke",
            phone="+254700000001",
            id_number=1001,
        )
        credential = make_credential(staff, "jane")
        session, _ = services.open_staff_session(credential)

        principal = StaffPrincipal(session)
        self.assertEqual(tenant_scope(principal), [self.a.pk])

    def test_an_unauthenticated_caller_sees_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(tenant_scope(AnonymousUser()), [])
        self.assertEqual(tenant_scope(None), [])

    def test_scoped_returns_empty_rather_than_forbidden(self):
        """
        A read of somebody else's row comes back EMPTY. A 403 would confirm
        the row exists, which is the same leak with a different status code.
        """
        from django.contrib.auth.models import AnonymousUser

        everything = BusinessOrganization.objects.all()
        self.assertEqual(scoped(everything, AnonymousUser(), field="id").count(), 0)

    def test_a_subscriber_sees_only_the_tenants_they_belong_to(self):
        account = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@a.co.ke"
        )
        TenantMembership.objects.create(account=account, organization=self.a)

        visible = scoped(BusinessOrganization.objects.all(), account, field="id")
        self.assertEqual([o.pk for o in visible], [self.a.pk])


# ── the doors, over HTTP ────────────────────────────────────────────────────


class EndpointTests(TestCase):
    def test_whoami_refuses_an_unauthenticated_caller(self):
        self.assertIn(self.client.get(reverse("whoami")).status_code, (401, 403))

    def test_staff_sign_in_refuses_a_made_up_shop(self):
        response = self.client.post(
            reverse("staff-sign-in"),
            {"organization": 999999, "username": "jane", "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_a_till_token_is_not_a_subscriber_session(self):
        """
        The two credential stores must never meet. A staff token presented as
        anything other than a bearer token gets nothing.
        """
        shop = make_org("Shop A")
        staff = make_staff(
            shop,
            full_name="Jane Cashier",
            email="jane@a.co.ke",
            phone="+254700000001",
            id_number=1001,
        )
        credential = make_credential(staff, "jane")
        _, token = services.open_staff_session(credential)

        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = token
        session.save()

        self.assertIn(self.client.get(reverse("whoami")).status_code, (401, 403))


class HealthCheckTests(TestCase):
    """
    Caddy reads /healthz every ten seconds and takes the app out of rotation
    when it fails (deploy/business.caddy).

    The first version of that block pointed at /auth/me, which returns 403 to
    an anonymous caller — Caddy would have marked the app permanently unhealthy
    and served 502 to everybody. Anything requiring a credential cannot be a
    health check.
    """

    def test_it_answers_without_a_credential(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_it_says_nothing_about_the_system(self):
        """A public endpoint is not a place to report versions or hostnames."""
        body = self.client.get("/healthz").content.decode()
        self.assertEqual(len(body), len('{"status": "ok"}'))
