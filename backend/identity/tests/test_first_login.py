"""
Somebody's first day: a login they can set up themselves, and a landing.

═══════════════════════════════════════════════════════════════════════════════
THE TWO THINGS THIS FILE IS ACTUALLY DEFENDING.

  1. A MANAGER NEVER HAS TO KNOW THE PASSWORD. The old path required one on
     create, so every shop's first act was to invent a credential two people
     held — and `must_change_password` existed to flag how unsatisfactory that
     was rather than to fix it. A login created without a password cannot be
     signed into BY ANYBODY, including the manager who made it, and the only
     way in is a code sent to the employee.

  2. A SIGNED-IN PERSON IS SENT SOMEWHERE THAT WORKS. The till used to derive
     that from the permission list and got it wrong for every role it was not
     written for: a purchasing officer holds neither `sales.checkout` nor
     `inventory.count`, fell through to the register chooser, and was shown
     "could not reach the shop's records" — a 403 on a screen they should
     never have reached, reported as a network fault, for ever.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from branches.models import Branches, staffAssignment
from identity import access, services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import (
    SETUP_CODE_LIFETIME,
    PlatformAccount,
    StaffCredential,
    StaffPasswordReset,
    TenantMembership,
)
from organisations.models import BusinessOrganization, OrganizationStaff

GOOD_PASSWORD = "not-a-real-password"

MAIL_TO_MEMORY = override_settings(
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
)


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


def a_staff(org, *, name, id_number, email=None):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email="" if email == "" else (email or f"{id_number}@shop.co.ke"),
        phone_number=f"+2547{id_number:08d}",
        address="Nairobi",
        id_number=id_number,
    )


class FirstDayBase(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Kilimani Dental")
        self.jane = a_staff(self.org, name="Jane Mwangi", id_number=10000001)
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="CA"
        )

        self.owner = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop.co.ke"
        )
        TenantMembership.objects.create(
            account=self.owner,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )

    def as_owner(self):
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.owner.pk
        session.save()

    def make_login(self, *, staff=None, username="jmwangi", password=None):
        """Through the API, because the point is the shape of the request."""
        self.as_owner()
        body = {"staff": (staff or self.jane).pk, "username": username}
        if password:
            body["password"] = password
        return self.client.post(
            "/auth/staff/credentials/", body, content_type="application/json"
        )


@MAIL_TO_MEMORY
class CreatingALoginWithoutAPasswordTests(FirstDayBase):
    def test_a_login_can_be_created_with_no_password_at_all(self):
        response = self.make_login()
        self.assertEqual(response.status_code, 201, response.content)

        credential = StaffCredential.objects.get(username="jmwangi")
        self.assertTrue(credential.needs_setup)
        self.assertIs(response.json()["needs_setup"], True)

    def test_nobody_can_sign_in_to_a_login_that_has_no_password(self):
        """
        The whole of the protection. A manager who creates a login this way
        has not created a credential they could use.
        """
        self.make_login()
        credential = StaffCredential.objects.get(username="jmwangi")

        # Including the stored hash itself, which is the one value somebody
        # reading the database would actually have.
        for attempt in ("", " ", GOOD_PASSWORD, credential.password):
            with self.subTest(attempt=attempt[:12]):
                refused = Client().post(
                    "/auth/staff/sign-in",
                    {
                        "organization": self.org.pk,
                        "username": "jmwangi",
                        "password": attempt,
                    },
                    content_type="application/json",
                )
                self.assertEqual(refused.status_code, 401, refused.content)

    def test_creating_it_emails_a_setup_code_and_says_so(self):
        response = self.make_login()

        self.assertIs(response.json()["invitation"]["sent"], True)
        self.assertIn(self.jane.email, response.json()["invitation"]["detail"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.jane.email])

    def test_the_email_carries_the_business_number_and_no_password(self):
        """
        ⚠ Staff sign-in takes the organisation as a parameter and there is no
          endpoint that lists shops. Without the number in here, the only
          message a new employee ever receives describes a screen they cannot
          get past.
        """
        self.make_login()
        body = mail.outbox[0].body

        self.assertIn(str(self.org.pk), body)
        self.assertIn("jmwangi", body)
        self.assertIn("First time here?", body)

        code = StaffPasswordReset.objects.get().code_hashed
        self.assertNotIn(code, body)

    def test_the_code_is_a_setup_code_and_lasts_longer_than_a_reset(self):
        """
        A manager sets somebody up on Friday for a Monday shift. Fifteen
        minutes is a code that died over the weekend.
        """
        self.make_login()
        row = StaffPasswordReset.objects.get()

        self.assertEqual(row.purpose, StaffPasswordReset.Purpose.SETUP)
        self.assertGreater(
            row.expires_at - timezone.now(), StaffPasswordReset.LIFETIME
        )
        self.assertLessEqual(row.expires_at - timezone.now(), SETUP_CODE_LIFETIME)

    def test_a_manager_can_still_set_a_password_when_there_is_no_email(self):
        """
        The path that must not be removed. A shop with an employee who has no
        address on file still has to be able to put them on a till.
        """
        nophone = a_staff(self.org, name="No Email", id_number=10000002, email="")
        staffAssignment.objects.create(
            staff_member=nophone, branch=self.branch, staff_assignment="CA"
        )

        response = self.make_login(
            staff=nophone, username="noemail", password=GOOD_PASSWORD
        )
        self.assertEqual(response.status_code, 201, response.content)

        credential = StaffCredential.objects.get(username="noemail")
        self.assertFalse(credential.needs_setup)
        self.assertTrue(credential.must_change_password)
        # No invitation was attempted, so none is reported.
        self.assertNotIn("invitation", response.json())
        self.assertEqual(len(mail.outbox), 0)

    def test_a_short_password_is_still_refused_when_one_is_sent(self):
        """
        The floor applies to a password, and must not apply to its absence —
        an empty field is the invitation path, not a bad password.
        """
        response = self.make_login(password="short")
        self.assertEqual(response.status_code, 400, response.content)

    def test_no_email_on_file_is_reported_rather_than_silently_dropped(self):
        """
        Unlike the public reset, which answers identically whatever happened.
        A manager holds staff.manage over their own employee's record and can
        act on this; silence would leave them watching for an email that was
        never going anywhere.
        """
        nobody = a_staff(self.org, name="No Email", id_number=10000003, email="")
        staffAssignment.objects.create(
            staff_member=nobody, branch=self.branch, staff_assignment="CA"
        )

        response = self.make_login(staff=nobody, username="noemail")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIs(response.json()["invitation"]["sent"], False)
        self.assertIn("no email address on file", response.json()["invitation"]["detail"])

        # ⚠ AND THE LOGIN STILL EXISTS. A mail failure that rolled this back
        #   would leave the manager with an error, no credential, and a
        #   username that is free again.
        self.assertTrue(StaffCredential.objects.filter(username="noemail").exists())


@MAIL_TO_MEMORY
class SettingUpTheLoginTests(FirstDayBase):
    def setUp(self):
        super().setUp()
        self.make_login()
        self.code = self.last_code()
        mail.outbox.clear()

    def last_code(self):
        """
        Re-mint, because the plaintext exists only in the email and the row is
        hashed — which is the property worth preserving rather than working
        around. Re-minting burns the first code, which is also the behaviour
        under test elsewhere.
        """
        minted = services.request_password_reset(
            organization_id=self.org.pk, username="jmwangi"
        )
        return minted.code

    def confirm(self, **overrides):
        body = {
            "organization": self.org.pk,
            "username": "jmwangi",
            "code": self.code,
            "new_password": GOOD_PASSWORD,
            **overrides,
        }
        return Client().post(
            "/auth/staff/password/reset/confirm", body, content_type="application/json"
        )

    def test_setting_it_up_signs_them_straight_in(self):
        """
        The ask, and the reason this differs from the forgotten-password path:
        there is no password they used to know, so re-typing the new one
        proves nothing, and it is their first morning in a shop.
        """
        response = self.confirm()
        self.assertEqual(response.status_code, 201, response.content)

        body = response.json()
        self.assertTrue(body["token"])
        self.assertEqual(body["staff"]["id"], self.jane.pk)
        self.assertEqual(body["organisation"]["id"], self.org.pk)

    def test_the_session_it_returns_actually_works(self):
        token = self.confirm().json()["token"]

        me = Client().get("/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(me.status_code, 200, me.content)
        self.assertEqual(me.json()["username"], "jmwangi")

    def test_it_says_where_to_send_them(self):
        """A landing, not a permission list to interpret."""
        self.assertEqual(self.confirm().json()["workspaces"], ["sell"])

    def test_the_password_they_chose_is_solely_theirs(self):
        """
        `must_change_password` is FALSE, unlike the manager-typed path. The
        flag answers "has the person themselves chosen this", and here they
        have.
        """
        self.confirm()
        credential = StaffCredential.objects.get(username="jmwangi")

        self.assertFalse(credential.must_change_password)
        self.assertFalse(credential.needs_setup)
        self.assertTrue(credential.check_password(GOOD_PASSWORD))

    def test_a_second_code_cannot_be_a_setup_code_once_they_are_set_up(self):
        """
        ⚠ THIS IS WHAT BOUNDS THE THREE-DAY WINDOW.

        Three days is defensible because a setup code is only ever minted for
        a credential with no usable password. Choosing one closes that state,
        so nothing can mint another long-lived code against a live login.
        """
        self.confirm()

        minted = services.request_password_reset(
            organization_id=self.org.pk, username="jmwangi"
        )
        self.assertEqual(minted.purpose, StaffPasswordReset.Purpose.RESET)
        self.assertLessEqual(
            minted.expires_at - timezone.now(), StaffPasswordReset.LIFETIME
        )

    def test_a_wrong_code_still_refuses_uniformly(self):
        refused = self.confirm(code="000000")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()["detail"], services.RESET_REFUSED)

    def test_a_forgotten_password_is_still_not_signed_in(self):
        """
        The distinction, from the other side. Once the login is live, a reset
        goes back through sign-in — the decision that was already written
        down, and that this change deliberately leaves alone.
        """
        self.confirm()

        again = services.request_password_reset(
            organization_id=self.org.pk, username="jmwangi"
        )
        response = Client().post(
            "/auth/staff/password/reset/confirm",
            {
                "organization": self.org.pk,
                "username": "jmwangi",
                "code": again.code,
                "new_password": "a-different-one-entirely",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204, response.content)


@MAIL_TO_MEMORY
class ResendingTests(FirstDayBase):
    def test_the_invite_action_resends_a_first_time_code(self):
        """For the email that bounced, and the person who never got round to it."""
        self.make_login()
        credential = StaffCredential.objects.get(username="jmwangi")
        mail.outbox.clear()

        self.as_owner()
        response = self.client.post(
            f"/auth/staff/credentials/{credential.pk}/invite/",
            {},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202, response.content)
        self.assertIn("First time here?", mail.outbox[0].body)

    def test_resending_to_a_live_login_sends_the_reset_letter_instead(self):
        """
        Same button, different letter. "You asked to reset your password" on
        somebody's first morning is a message they cannot act on — and
        "you have been set up to work here" to somebody three months in is
        just as useless.
        """
        self.make_login(password=GOOD_PASSWORD, username="live")
        credential = StaffCredential.objects.get(username="live")
        credential.set_password(GOOD_PASSWORD)
        credential.save(update_fields=["password"])
        mail.outbox.clear()

        self.as_owner()
        self.client.post(
            f"/auth/staff/credentials/{credential.pk}/invite/",
            {},
            content_type="application/json",
        )
        body = mail.outbox[0].body
        self.assertIn("Forgotten your password?", body)
        self.assertNotIn("First time here?", body)

    def test_a_manager_can_set_a_password_on_a_login_awaiting_setup(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE WAY OUT AT SEVEN IN THE MORNING.

        `subscriptions/entitlement.py` settled this in another costume: a POS
        that will not open is a shop that cannot sell, and refusing to trade
        is the worse failure. A login that can only be opened by an email is
        a login that mail trouble can keep off a till.

        So the manager path works on a credential in setup state exactly as
        it does on a live one. The cost is the one it always was — they know
        the password — which is why `must_change_password` goes back to true.
        ══════════════════════════════════════════════════════════════════════
        """
        self.make_login()
        credential = StaffCredential.objects.get(username="jmwangi")
        self.assertTrue(credential.needs_setup)

        self.as_owner()
        response = self.client.post(
            f"/auth/staff/credentials/{credential.pk}/reset-password/",
            {"password": GOOD_PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        signed_in = Client().post(
            "/auth/staff/sign-in",
            {
                "organization": self.org.pk,
                "username": "jmwangi",
                "password": GOOD_PASSWORD,
            },
            content_type="application/json",
        )
        self.assertEqual(signed_in.status_code, 201, signed_in.content)

        credential.refresh_from_db()
        self.assertFalse(credential.needs_setup)
        self.assertTrue(credential.must_change_password)

    def test_a_withdrawn_login_is_not_invited(self):
        self.make_login()
        credential = StaffCredential.objects.get(username="jmwangi")
        services.set_credential_active(credential=credential, active=False)
        mail.outbox.clear()

        self.as_owner()
        response = self.client.post(
            f"/auth/staff/credentials/{credential.pk}/invite/",
            {},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(len(mail.outbox), 0)


class WhereTheyLandTests(FirstDayBase):
    """
    ════════════════════════════════════════════════════════════════════════
    THE ROLE TABLE AND THE LANDING MUST NOT BE ABLE TO DISAGREE.

    `access.workspaces` reads the same `OPERATIONAL_ROLES` map every
    permission check reads, so a role whose grants change cannot keep an old
    destination. These tests are the other half: they pin the ANSWER for
    every role the product offers, so adding one without thinking about where
    its holder lands is a failure here rather than a 403 in a shop.
    ════════════════════════════════════════════════════════════════════════
    """

    def signed_in_as(self, role, *, number):
        person = a_staff(self.org, name=f"Role {role}", id_number=number)
        staffAssignment.objects.create(
            staff_member=person, branch=self.branch, staff_assignment=role
        )
        credential = services.issue_credential(
            staff=person, username=f"u{number}", password=GOOD_PASSWORD
        )
        _, token = services.open_staff_session(credential)
        return token

    def test_every_role_the_product_offers_has_a_decided_landing(self):
        """
        ⚠ A NEW ROLE MUST APPEAR HERE. An operational role missing from this
          map is a role whose holder's first morning nobody has thought about.
        """
        expected = {
            "CA": ["sell"],            # cashier
            "SA": ["sell"],            # sales associate — a cashier by another name
            "AM": ["sell", "count"],   # assistant manager, who genuinely does both
            "IC": ["count"],           # inventory clerk: counts, cannot sell
            # ── THE THREE WITH NOWHERE TO GO, STATED RATHER THAN DISCOVERED ──
            # Each holds real permissions against endpoints that honour them.
            # What none of them has is a TILL screen, so the honest answer is
            # an empty list and the client says so. These entries are the
            # record of a gap, and they are what will fail when it is closed.
            "PO": [],                  # purchasing officer
            "FC": [],                  # finance clerk
            "BA": [],                  # branch auditor
        }
        self.assertEqual(
            sorted(expected), sorted(access.OPERATIONAL_ROLES),
            "An operational role has no decided landing. Add it here.",
        )

        for number, (role, landing) in enumerate(expected.items(), start=20000001):
            with self.subTest(role=role):
                token = self.signed_in_as(role, number=number)
                me = Client().get("/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
                self.assertEqual(me.status_code, 200, me.content)
                self.assertEqual(me.json()["workspaces"], landing)

    def test_a_role_with_no_till_screen_is_still_a_valid_session(self):
        """
        ⚠ EMPTY IS NOT "REFUSED". A purchasing officer signs in correctly and
          the API answers them; what is missing is a screen. A client that
          renders this as a failed sign-in tells somebody their login is
          broken when the truth is that we have not built their workspace.
        """
        token = self.signed_in_as("PO", number=30000001)

        me = Client().get("/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(me.json()["workspaces"], [])
        self.assertIn("purchasing.receive", me.json()["permissions"])

    def test_sign_in_and_whoami_agree_about_the_landing(self):
        """
        Two doors, one answer. They were separate dictionaries and the till
        reads both into the same stored session, so a field added to one and
        not the other sends somebody to a screen with half its data.
        """
        person = a_staff(self.org, name="Both Jobs", id_number=40000001)
        staffAssignment.objects.create(
            staff_member=person, branch=self.branch, staff_assignment="AM"
        )
        services.issue_credential(
            staff=person, username="manager", password=GOOD_PASSWORD
        )

        signed_in = Client().post(
            "/auth/staff/sign-in",
            {
                "organization": self.org.pk,
                "username": "manager",
                "password": GOOD_PASSWORD,
            },
            content_type="application/json",
        )
        self.assertEqual(signed_in.status_code, 201, signed_in.content)
        token = signed_in.json()["token"]

        me = Client().get("/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(signed_in.json()["workspaces"], me.json()["workspaces"])
        self.assertEqual(me.json()["workspaces"], ["sell", "count"])

    def test_selling_comes_before_counting(self):
        """
        Order is the landing order, not a set. A manager who does both is sent
        to the register first because a queue cannot wait and a shelf can.
        """
        self.assertEqual([name for name, _ in access.WORKSPACES][0], "sell")

    def test_a_workspace_is_only_named_where_it_is_held_at_that_branch(self):
        """
        One person can be a cashier at Westlands and the manager at Karen.
        The union would offer the Karen landing at a Westlands terminal.
        """
        other = Branches.objects.create(
            organization=self.org,
            branch_name="Karen",
            branch_location="Nairobi",
            branch_allocation="First floor",
            branch_manager="A Manager",
            is_active=True,
        )
        person = a_staff(self.org, name="Two Branches", id_number=50000001)
        staffAssignment.objects.create(
            staff_member=person, branch=self.branch, staff_assignment="IC"
        )
        staffAssignment.objects.create(
            staff_member=person, branch=other, staff_assignment="CA"
        )
        credential = services.issue_credential(
            staff=person, username="twohats", password=GOOD_PASSWORD
        )
        session, _ = services.open_staff_session(credential)

        from identity.authentication import StaffPrincipal

        principal = StaffPrincipal(session)
        self.assertEqual(access.workspaces(principal, self.branch.pk), ["count"])
        self.assertEqual(access.workspaces(principal, other.pk), ["sell"])
        # The union, which is the right answer to "anywhere?" and the wrong
        # one to "here?".
        self.assertEqual(access.workspaces(principal), ["sell", "count"])
