"""
Issuing a till login — the last step of the product, and the one with the most
ways to become a hole.

A credential here is a password that opens somebody's shop. The questions worth
testing are therefore not "can a manager make one" but: can they make one
pointing at ANOTHER shop's employee, can anybody read the hash back out, does
the username constraint tell one shop about another's staff, and does taking a
login away actually take it away — now, not at expiry.
"""

from __future__ import annotations

from django.test import Client, TestCase

from branches.models import Branches, staffAssignment
from identity import services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, StaffCredential, StaffSession, TenantMembership
from organisations.models import BusinessOrganization, OrganizationStaff

GOOD_PASSWORD = "not-a-real-password"


def a_shop(name):
    org = BusinessOrganization.objects.create(name=name)
    branch = Branches.objects.create(
        organization=org,
        branch_name=f"{name} Main",
        branch_location="Nairobi",
        branch_allocation="Ground floor",
        branch_manager="A Manager",
        is_active=True,
    )
    return org, branch


def a_staff(org, *, name, id_number):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email=f"{id_number}@shop.co.ke",
        phone_number=f"+2547{id_number:08d}",
        address="Nairobi",
        id_number=id_number,
    )


class CredentialIssuingTests(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Shop A")
        self.other, self.other_branch = a_shop("Shop B")

        self.jane = a_staff(self.org, name="Jane Cashier", id_number=10000001)
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="CA"
        )
        self.stranger = a_staff(self.other, name="Someone Else", id_number=20000002)

        self.owner = self.subscriber(self.org, number=1)

    def subscriber(self, org, *, number, role=TenantMembership.Role.OWNER):
        account = PlatformAccount.objects.create(
            genmars_account_id=number, email=f"owner{number}@shop.co.ke"
        )
        TenantMembership.objects.create(account=account, organization=org, role=role)
        return account

    def sign_in(self, account):
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

    # ── the control ─────────────────────────────────────────────────────────

    def test_an_owner_issues_a_login_and_the_cashier_can_use_it(self):
        """
        End to end, because every refusal below is only meaningful beside a
        path that works. This is also the step that was unreachable: a shop
        could finish onboarding and still have nobody able to open a till.
        """
        self.sign_in(self.owner)

        response = self.client.post(
            "/auth/staff/credentials/",
            {"staff": self.jane.pk, "username": "jmwangi", "password": GOOD_PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        signed_in = self.client.post(
            "/auth/staff/sign-in",
            {
                "organization": self.org.pk,
                "username": "jmwangi",
                "password": GOOD_PASSWORD,
            },
            content_type="application/json",
        )
        self.assertEqual(signed_in.status_code, 201, signed_in.content)
        self.assertTrue(signed_in.json().get("token"))

    def test_the_organisation_is_derived_and_cannot_be_supplied(self):
        """
        Blueprint §8. A caller who could name the organisation could hang a
        login they control on somebody else's shop — and staff sign-in takes
        the organisation as a parameter, so it would then WORK there.
        """
        self.sign_in(self.owner)

        response = self.client.post(
            "/auth/staff/credentials/",
            {
                "staff": self.jane.pk,
                "username": "jmwangi",
                "password": GOOD_PASSWORD,
                "organization": self.other.pk,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        credential = StaffCredential.objects.get(username="jmwangi")
        self.assertEqual(credential.organization_id, self.org.pk)

    def test_a_login_cannot_be_issued_for_another_shops_employee(self):
        self.sign_in(self.owner)

        response = self.client.post(
            "/auth/staff/credentials/",
            {
                "staff": self.stranger.pk,
                "username": "intruder",
                "password": GOOD_PASSWORD,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(StaffCredential.objects.filter(username="intruder").exists())

    def test_the_hash_is_never_in_a_response(self):
        """
        Not "is it write_only in the serialiser" — that is the mechanism. This
        asks the question that matters: does an Argon2 hash reach a client
        through any of the four ways out of this viewset.
        """
        self.sign_in(self.owner)
        made = self.client.post(
            "/auth/staff/credentials/",
            {"staff": self.jane.pk, "username": "jmwangi", "password": GOOD_PASSWORD},
            content_type="application/json",
        )
        credential = StaffCredential.objects.get(username="jmwangi")
        self.assertTrue(credential.password.startswith("argon2"))

        bodies = [
            made.content,
            self.client.get("/auth/staff/credentials/").content,
            self.client.get(f"/auth/staff/credentials/{credential.pk}/").content,
            self.client.post(
                f"/auth/staff/credentials/{credential.pk}/reset-password/",
                {"password": "another-fake-password"},
                content_type="application/json",
            ).content,
        ]
        for body in bodies:
            self.assertNotIn(b"argon2", body)
            self.assertNotIn(GOOD_PASSWORD.encode(), body)


class UsernameIsolationTests(TestCase):
    """
    A uniqueness constraint is an oracle, and this is the one most likely to
    become one: the manager of Shop A types a username and the error tells them
    whether Shop B employs somebody by that name.
    """

    def setUp(self):
        self.a, _ = a_shop("Shop A")
        self.b, _ = a_shop("Shop B")
        self.jane = a_staff(self.a, name="Jane", id_number=30000003)
        self.john = a_staff(self.b, name="John", id_number=40000004)

        services.issue_credential(
            staff=self.john, username="jmwangi", password=GOOD_PASSWORD
        )

    def test_two_shops_may_both_employ_a_jmwangi(self):
        credential = services.issue_credential(
            staff=self.jane, username="jmwangi", password=GOOD_PASSWORD
        )
        self.assertEqual(credential.organization_id, self.a.pk)
        self.assertEqual(StaffCredential.objects.filter(username="jmwangi").count(), 2)

    def test_a_username_taken_in_the_same_shop_is_refused_case_insensitively(self):
        """
        The positive control's opposite — and case-insensitive, because
        `JMwangi` signing in as `jmwangi` is the same person to everybody
        except the database.
        """
        second = a_staff(self.a, name="Jane Two", id_number=30000009)
        services.issue_credential(
            staff=self.jane, username="jmwangi", password=GOOD_PASSWORD
        )

        with self.assertRaises(services.CredentialError):
            services.issue_credential(
                staff=second, username="JMwangi", password=GOOD_PASSWORD
            )


class WithdrawingALoginTests(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Shop A")
        self.jane = a_staff(self.org, name="Jane", id_number=50000005)
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="CA"
        )
        self.credential = services.issue_credential(
            staff=self.jane, username="jmwangi", password=GOOD_PASSWORD
        )
        _, self.token = services.open_staff_session(self.credential)
        # Its OWN client. Sharing one with the owner below would send both a
        # session cookie and a bearer token, and the cookie wins — every
        # refusal here would then be measured against the owner.
        self.register = Client()

        self.owner = PlatformAccount.objects.create(
            genmars_account_id=7, email="owner@shop.co.ke"
        )
        TenantMembership.objects.create(
            account=self.owner,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.owner.pk
        session.save()

    def till(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def still_signed_in(self) -> bool:
        return self.register.get("/auth/me", **self.till()).status_code == 200

    def test_the_session_works_first(self):
        """Without this, every assertion below passes against a broken till."""
        self.assertTrue(self.still_signed_in())

    def test_withdrawing_the_login_ends_the_shift_immediately(self):
        """
        Not at expiry. The moment somebody is walked off the premises is the
        moment this has to take effect — a till left signed in at the counter
        is exactly where the old session would be used.
        """
        response = self.client.post(
            f"/auth/staff/credentials/{self.credential.pk}/set-active/",
            {"is_active": False},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.assertFalse(self.still_signed_in())

    def test_resetting_the_password_also_ends_it(self):
        """
        The usual reason to reset is that somebody else may know the old one.
        Leaving their sessions running protects nobody.
        """
        response = self.client.post(
            f"/auth/staff/credentials/{self.credential.pk}/reset-password/",
            {"password": "a-different-fake-password"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(self.still_signed_in())

    def test_a_bearer_token_beside_a_session_cookie_does_not_become_a_second_way_in(self):
        """
        Both credentials on one request. DRF walks the authentication classes
        in order and stops at the first that answers, so the session cookie
        wins and the token is never looked at.

        Pinned because it reads like an oversight and is the safe half of the
        pair: the answer that matters is that the till's token can never
        ESCALATE a request — it cannot turn the owner's session into
        something else, and it cannot lend its own authority to one.
        """
        self.register.cookies = self.client.cookies  # the owner's session
        response = self.register.get("/auth/me", **self.till())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kind"], "subscriber")

    def test_a_login_cannot_be_deleted(self):
        """
        Blueprint §10. A sale records who rang it up; that is worth nothing if
        the cashier can be removed from under it. Leaving is is_active: false.
        """
        response = self.client.delete(
            f"/auth/staff/credentials/{self.credential.pk}/"
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(StaffCredential.objects.filter(pk=self.credential.pk).exists())

    def test_a_short_password_is_refused(self):
        response = self.client.post(
            f"/auth/staff/credentials/{self.credential.pk}/reset-password/",
            {"password": "short"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        # And the old one still works — a refused reset must not half-apply.
        self.assertTrue(
            StaffCredential.objects.get(pk=self.credential.pk).check_password(
                GOOD_PASSWORD
            )
        )


class WhoMayIssueTests(TestCase):
    """
    STAFF_MANAGE is owner-only today. A cashier who could issue logins could
    make themselves a second one with a different name and ring up sales as
    somebody who does not exist.
    """

    def setUp(self):
        self.org, self.branch = a_shop("Shop A")
        self.jane = a_staff(self.org, name="Jane", id_number=60000006)
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="CA"
        )
        self.credential = services.issue_credential(
            staff=self.jane, username="jmwangi", password=GOOD_PASSWORD
        )
        _, self.token = services.open_staff_session(self.credential)
        self.new = a_staff(self.org, name="Accomplice", id_number=60000007)

    def till(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def test_a_cashier_may_not_issue_a_login(self):
        response = self.client.post(
            "/auth/staff/credentials/",
            {"staff": self.new.pk, "username": "ghost", "password": GOOD_PASSWORD},
            content_type="application/json",
            **self.till(),
        )
        self.assertIn(response.status_code, (403, 404), response.content)
        self.assertFalse(StaffCredential.objects.filter(username="ghost").exists())

    def test_a_cashier_may_not_list_them(self):
        response = self.client.get("/auth/staff/credentials/", **self.till())
        self.assertEqual(response.status_code, 403, response.content)

    def test_an_anonymous_caller_may_not_either(self):
        response = self.client.post(
            "/auth/staff/credentials/",
            {"staff": self.new.pk, "username": "ghost", "password": GOOD_PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403, response.content)


class ChangingYourOwnPasswordTests(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Shop A")
        self.jane = a_staff(self.org, name="Jane", id_number=70000007)
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="CA"
        )
        self.credential = services.issue_credential(
            staff=self.jane, username="jmwangi", password=GOOD_PASSWORD
        )
        _, self.token = services.open_staff_session(self.credential)

    def till(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def test_a_new_login_starts_out_needing_a_change(self):
        """
        The manager typed it, so they know it, so nothing done under it is
        solely attributable to the cashier. /auth/me says so, because that is
        where the till learns to draw the screen.
        """
        me = self.client.get("/auth/me", **self.till()).json()
        self.assertTrue(me["must_change_password"])

    def test_changing_it_clears_the_flag_and_the_old_password_stops_working(self):
        response = self.client.post(
            "/auth/staff/password",
            {
                "current_password": GOOD_PASSWORD,
                "new_password": "chosen-by-jane-herself",
            },
            content_type="application/json",
            **self.till(),
        )
        self.assertEqual(response.status_code, 204, response.content)

        me = self.client.get("/auth/me", **self.till()).json()
        self.assertFalse(me["must_change_password"])

        with self.assertRaises(services.AuthError):
            services.authenticate_staff(
                organization_id=self.org.pk,
                username="jmwangi",
                password=GOOD_PASSWORD,
            )

    def test_the_current_password_is_required(self):
        """
        A till left unattended is the normal state of a till. Without this,
        anybody walking past could lock the cashier out of their own login.
        """
        response = self.client.post(
            "/auth/staff/password",
            {"current_password": "wrong", "new_password": "chosen-by-someone-else"},
            content_type="application/json",
            **self.till(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            StaffCredential.objects.get(pk=self.credential.pk).check_password(
                GOOD_PASSWORD
            )
        )

    def test_there_is_no_way_to_name_somebody_elses_credential(self):
        """
        The endpoint takes no id. This pins that: whatever a caller sends, the
        credential changed is the one their session belongs to.
        """
        other = a_staff(self.org, name="Other", id_number=70000008)
        others = services.issue_credential(
            staff=other, username="other", password=GOOD_PASSWORD
        )

        self.client.post(
            "/auth/staff/password",
            {
                "current_password": GOOD_PASSWORD,
                "new_password": "chosen-by-jane-herself",
                "credential": others.pk,
                "id": others.pk,
                "username": "other",
            },
            content_type="application/json",
            **self.till(),
        )

        self.assertTrue(
            StaffCredential.objects.get(pk=others.pk).check_password(GOOD_PASSWORD)
        )

    def test_a_subscriber_has_no_password_here_to_change(self):
        account = PlatformAccount.objects.create(
            genmars_account_id=88, email="owner@shop.co.ke"
        )
        TenantMembership.objects.create(
            account=account,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

        response = self.client.post(
            "/auth/staff/password",
            {"current_password": "x", "new_password": "y"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
