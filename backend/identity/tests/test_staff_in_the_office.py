"""
Operational staff working in a browser instead of at a till.

═══════════════════════════════════════════════════════════════════════════════
THE SAME CREDENTIAL, CARRIED DIFFERENTLY — AND THE THREE WAYS THAT GOES WRONG.

A bearer token was right for a till and nothing about that argument reaches a
finance clerk on a laptop, who had nowhere to work: every office screen is
server-rendered, a server component forwards cookies rather than headers, and
so the application that was already written for them (Shell.tsx gates every
nav item on a permission) refused them at the door.

Adding a second transport for one credential is where the mistakes live:

  1. A session holding BOTH principals. Which one a request authenticated as
     would then depend on the order of DEFAULT_AUTHENTICATION_CLASSES — a line
     in settings.py nobody reads as a security control.
  2. A cookie without CSRF. A cookie is sent automatically, so a cross-site
     POST rides on it; the bearer class deliberately does not enforce CSRF and
     must not, because a header is never sent automatically.
  3. The tiers meeting. A subscriber must not be able to sign in this way and
     a cashier's credential must still be refused everywhere it was before.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from branches.models import Branches, staffAssignment
from django.test import Client, TestCase

from identity import access, services
from identity.authentication import STAFF_SESSION_KEY, SUBSCRIBER_SESSION_KEY
from identity.models import (
    PlatformAccount,
    StaffCredential,
    StaffSession,
    TenantMembership,
)
from organisations.models import BusinessOrganization, OrganizationStaff

PASSWORD = "till-password-not-real"


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


class Base(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Kilimani Dental")

        self.person = OrganizationStaff.objects.create(
            organization=self.org,
            full_name="Fay Clerk",
            email="fay@shop.co.ke",
            phone_number="+254700000001",
            address="Nairobi",
            id_number=10000001,
        )
        # A finance clerk: reads the money, touches none of it, and has no
        # till workspace — the role this whole change exists for.
        staffAssignment.objects.create(
            staff_member=self.person, branch=self.branch, staff_assignment="FC"
        )
        self.credential = services.issue_credential(
            staff=self.person, username="fclerk", password=PASSWORD
        )

    def sign_in(self, client=None, **overrides):
        body = {
            "organization": self.org.pk,
            "username": "fclerk",
            "password": PASSWORD,
            **overrides,
        }
        return (client or self.client).post(
            "/auth/staff/session", body, content_type="application/json"
        )


class SigningInToTheOfficeTests(Base):
    def test_a_finance_clerk_can_sign_in_and_is_known(self):
        """
        The control. Every refusal below is only meaningful beside a path that
        works, and this is the path that did not exist at all.
        """
        response = self.sign_in()
        self.assertEqual(response.status_code, 201, response.content)

        me = self.client.get("/auth/me")
        self.assertEqual(me.status_code, 200, me.content)
        body = me.json()
        self.assertEqual(body["kind"], "staff")
        self.assertEqual(body["username"], "fclerk")
        # The permissions the office screens draw themselves from.
        self.assertIn(access.REPORTS_BRANCH, body["permissions"])
        self.assertNotIn(access.SALES_CHECKOUT, body["permissions"])

    def test_no_token_is_returned(self):
        """
        ⚠ A cookie session needs no second secret inside it — Django already
          signed the one the browser holds. Returning one would be a
          credential nobody uses, sitting in a response waiting to be found
          useful.
        """
        self.assertNotIn("token", self.sign_in().json())

    def test_it_opens_the_same_StaffSession_row_the_till_would(self):
        """
        One row, two transports. That is what makes `revoke_all_sessions` end
        both and `set_credential_active(False)` reach a browser.
        """
        self.sign_in()
        session = StaffSession.objects.get()
        self.assertEqual(session.credential, self.credential)
        self.assertEqual(
            self.client.session[STAFF_SESSION_KEY], session.pk
        )

    def test_a_wrong_password_is_refused_in_the_same_words_as_the_till(self):
        refused = self.sign_in(password="wrong")
        self.assertEqual(refused.status_code, 401, refused.content)
        self.assertEqual(
            refused.json()["detail"], services.GENERIC_SIGN_IN_FAILURE
        )

    def test_another_shops_organisation_number_does_not_work(self):
        other, _ = a_shop("Shop B")
        refused = self.sign_in(organization=other.pk)
        self.assertEqual(refused.status_code, 401, refused.content)


class OnlyOnePrincipalTests(Base):
    """
    ══════════════════════════════════════════════════════════════════════════
    A SESSION MUST NEVER HOLD BOTH KEYS.

    Both authentication classes read the same Django session. If both keys
    were set, which principal a request authenticated as would be decided by
    the ORDER of DEFAULT_AUTHENTICATION_CLASSES — and somebody reshuffling
    that list would change who a request is without touching anything that
    looks like authentication.
    ══════════════════════════════════════════════════════════════════════════
    """

    def subscriber(self):
        account = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop.co.ke"
        )
        TenantMembership.objects.create(
            account=account,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        return account

    def test_signing_in_as_staff_clears_a_subscriber_session(self):
        account = self.subscriber()
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

        self.sign_in()

        self.assertNotIn(SUBSCRIBER_SESSION_KEY, self.client.session)
        self.assertIn(STAFF_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get("/auth/me").json()["kind"], "staff")

    def test_signing_out_clears_both(self):
        self.sign_in()
        self.client.post("/auth/staff/sign-out", {}, content_type="application/json")

        self.assertNotIn(STAFF_SESSION_KEY, self.client.session)
        self.assertNotIn(SUBSCRIBER_SESSION_KEY, self.client.session)
        self.assertIn(self.client.get("/auth/me").status_code, (401, 403))

    def test_the_session_key_is_cycled_on_sign_in(self):
        """
        Session fixation. An id captured before the sign-in must not still be
        valid after it, and the sign-in is where closing that costs nothing.
        """
        self.client.get("/auth/me")
        before = self.client.session.session_key

        self.sign_in()

        self.assertNotEqual(self.client.session.session_key, before)


class RevocationReachesTheBrowserTests(Base):
    def test_withdrawing_the_login_signs_the_browser_out_at_once(self):
        """
        ⚠ Checked on every request rather than only at the door. The moment
          somebody is walked off the premises is the moment this has to take
          effect, not whenever their browser next signs in.
        """
        self.sign_in()
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

        services.set_credential_active(credential=self.credential, active=False)

        self.assertIn(self.client.get("/auth/me").status_code, (401, 403))
        self.assertNotIn(STAFF_SESSION_KEY, self.client.session)

    def test_a_manager_password_reset_ends_the_browser_session_too(self):
        """One StaffSession row, so one revocation path reaches both."""
        self.sign_in()
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

        services.reset_password(credential=self.credential, password="a-new-one-here")

        self.assertIn(self.client.get("/auth/me").status_code, (401, 403))


class CsrfTests(Base):
    """
    ══════════════════════════════════════════════════════════════════════════
    THE COOKIE TRANSPORT ENFORCES CSRF. THE TOKEN TRANSPORT MUST NOT.

    A cookie is sent by the browser automatically, so a cross-site POST rides
    on it. A bearer token is never sent automatically, so there is nothing to
    forge — and enforcing CSRF on it would break every till.

    That is why these are two authentication classes rather than one with a
    branch: a single class would decide per request whether the check applies,
    and the day that branch is wrong it fails open.
    ══════════════════════════════════════════════════════════════════════════
    """

    def test_a_cookie_write_without_a_csrf_token_is_refused(self):
        strict = Client(enforce_csrf_checks=True)
        self.sign_in(client=strict)

        # A write this principal would otherwise be allowed to attempt.
        refused = strict.post(
            "/auth/staff/password",
            {"current_password": PASSWORD, "new_password": "another-one-here"},
            content_type="application/json",
        )
        self.assertEqual(refused.status_code, 403, refused.content)
        self.assertIn("CSRF", str(refused.content))

    def test_the_same_write_succeeds_with_the_token(self):
        """
        The control — "refused" must not be able to mean "broken for
        everybody". The sign-in response carries the cookie, which is the
        whole reason it is decorated with ensure_csrf_cookie.
        """
        strict = Client(enforce_csrf_checks=True)
        signed_in = self.sign_in(client=strict)
        token = signed_in.cookies.get("csrftoken")
        self.assertIsNotNone(token, "sign-in must mint the CSRF cookie")

        allowed = strict.post(
            "/auth/staff/password",
            {"current_password": PASSWORD, "new_password": "another-one-here"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token.value,
        )
        self.assertEqual(allowed.status_code, 204, allowed.content)

    def test_a_till_token_still_needs_no_csrf(self):
        """
        ⚠ If this ever fails, every register in every shop has stopped
          working. The token transport is used by a client that cannot hold a
          CSRF cookie and must never be asked to.
        """
        _, token = services.open_staff_session(self.credential)
        strict = Client(enforce_csrf_checks=True)

        allowed = strict.post(
            "/auth/staff/password",
            {"current_password": PASSWORD, "new_password": "another-one-here"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(allowed.status_code, 204, allowed.content)


class TheTiersStillDoNotMeetTests(Base):
    def test_a_subscriber_cannot_sign_in_through_the_staff_door(self):
        """
        ⚠ The rule is that the two CREDENTIAL STORES never meet. This adds a
          transport, not a store: there is no Genmars account whose password
          this endpoint would accept, because it only ever consults
          StaffCredential.
        """
        PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop.co.ke"
        )
        refused = self.sign_in(username="owner@shop.co.ke", password="anything")
        self.assertEqual(refused.status_code, 401, refused.content)

    def test_a_staff_session_id_in_the_subscriber_key_is_not_an_account(self):
        """
        The shape of bug the subscriber class already guards: a value of the
        wrong kind in a session key must be refused rather than reach the ORM
        and 500.
        """
        _, _token = services.open_staff_session(self.credential)
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = "not-an-integer"
        session.save()

        self.assertIn(self.client.get("/auth/me").status_code, (401, 403))


class ACookieMustNotOutrankATokenTests(Base):
    """
    The till and the office are the SAME ORIGIN, and the browser does not
    know the difference.

    ═══════════════════════════════════════════════════════════════════════════
    THE FAILURE THE BANNER AT THE TOP OF THIS FILE PREDICTED, ARRIVED AT FROM
    THE OTHER SIDE.

    Item 1 up there says a request's principal must not depend on the order of
    DEFAULT_AUTHENTICATION_CLASSES. It was written about one SESSION holding
    two principals, and guarded there. What it did not cover is one BROWSER
    holding a cookie and a token at once — which needs no misuse at all:
    business.genmars.co.ke serves `/till` and `/sign-in`, Django's session
    cookie has no path restriction, and so the office cookie is attached to
    every request the till makes.

    DRF takes the first class that answers, and the two cookie classes are
    listed first. So the cookie won:

      · POST from the till → "CSRF failed: CSRF token missing". A bearer
        request carries no CSRF token and must never need one, so checkout,
        refunds, cash in and out and closing the drawer all stopped.
      · GET from the till → answered as the OFFICE user. A cashier's token
        went up and somebody else's name came back.

    One manager signing in on the shop's browser was enough to do it, and
    nothing on either screen would have said why.
    ═══════════════════════════════════════════════════════════════════════════
    """

    def setUp(self):
        super().setUp()
        # A second person, with a till login of their own — the cashier whose
        # token gets overridden.
        self.cashier = OrganizationStaff.objects.create(
            organization=self.org,
            full_name="Tom Cashier",
            email="tom@shop.co.ke",
            phone_number="+254700000002",
            address="Nairobi",
            id_number=10000002,
        )
        staffAssignment.objects.create(
            staff_member=self.cashier, branch=self.branch, staff_assignment="CA"
        )
        credential = services.issue_credential(
            staff=self.cashier, username="tom", password=PASSWORD
        )
        _, self.token = services.open_staff_session(credential)

    def till(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def test_the_token_names_the_cashier_even_with_an_office_cookie_present(self):
        """
        The one that matters. The till said Tom; the server used to say Fay.
        """
        self.sign_in()  # the office, in another tab of the same browser

        me = self.client.get("/auth/me", **self.till())
        self.assertEqual(me.status_code, 200, me.content)
        self.assertEqual(me.json()["username"], "tom", "the token was presented")

    def test_a_till_write_is_not_refused_for_a_missing_csrf_token(self):
        """
        A bearer request has no CSRF token and needs none — the cookie class
        was demanding one on its behalf. `enforce_csrf_checks=True` is the
        point of this test: the default client switches the check off, which
        is exactly why the suite never saw this.
        """
        client = Client(enforce_csrf_checks=True)
        self.sign_in(client)

        response = client.post(
            "/auth/staff/sign-out", {}, content_type="application/json",
            **self.till(),
        )
        self.assertNotEqual(response.status_code, 403, response.content)
        self.assertEqual(response.status_code, 204, response.content)

    def test_the_cookie_still_works_when_no_token_is_presented(self):
        """
        The control. Declining to a token must not break the office, which is
        the whole reason the cookie transport exists.
        """
        self.sign_in()
        me = self.client.get("/auth/me")
        self.assertEqual(me.status_code, 200, me.content)
        self.assertEqual(me.json()["username"], "fclerk")

    def test_the_office_still_enforces_csrf_on_its_own_writes(self):
        """
        ⚠ THE REGRESSION THIS CHANGE COULD EASILY HAVE INTRODUCED.

        If declining to an Authorization header were the only rule, a
        cross-site POST could skip CSRF simply by attaching a junk header.
        It cannot: without a cookie principal the request is anonymous, and
        the token class refuses the junk. But the cookie path itself must
        still demand a token, and that is what this asserts.
        """
        client = Client(enforce_csrf_checks=True)
        self.sign_in(client)

        refused = client.post(
            "/auth/staff/sign-out", {}, content_type="application/json"
        )
        self.assertEqual(refused.status_code, 403, refused.content)
        self.assertIn("CSRF", refused.json()["detail"])

    def test_a_junk_authorization_header_is_refused_rather_than_falling_back(self):
        """
        Fail CLOSED. Somebody who suppresses their cookie session with a
        bogus header does not get the cookie's authority back — they get
        nothing, which is a refusal and not an escalation.
        """
        self.sign_in()

        response = self.client.get(
            "/auth/me", HTTP_AUTHORIZATION="Bearer not-a-real-token"
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_a_subscriber_cookie_does_not_outrank_a_till_token_either(self):
        """
        Same rule, the other cookie class. An owner signed in to the office
        on the shop's browser must not become the principal behind the till.
        """
        owner = PlatformAccount.objects.create(
            genmars_account_id=99001, email="owner@shop.co.ke"
        )
        TenantMembership.objects.create(account=owner, organization=self.org)
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = owner.pk
        session.save()

        me = self.client.get("/auth/me", **self.till())
        self.assertEqual(me.status_code, 200, me.content)
        self.assertEqual(me.json()["kind"], "staff")
        self.assertEqual(me.json()["username"], "tom")
