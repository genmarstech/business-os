"""
Taking a return at the till.

═══════════════════════════════════════════════════════════════════════════════
THE DRAWER READ SHORT BY EVERY RETURN TAKEN, AND THAT IS WHY THIS EXISTS.

`refund_sale` has worked since module 9: it writes its own document, itemises
what came back, restocks what should go back on the shelf and leaves the sale
untouched. What was missing sat one layer down. Expected cash was

    opening + cash taken - change given + paid in - paid out

and a return handed over the counter appears in none of those terms. Hand back
1,500 in notes, count the drawer at six, and the till reports it 1,500 SHORT —
then notifications raise that shortfall to every holder of `reports.branch`.
Identical in shape to the bug CashMovement was written to fix, and reached by
the same route: a figure derived from part of what actually moved.

── WHICH MEANT `Refund` HAD TO LEARN HOW THE MONEY WENT BACK ─────────────────

It could not be inferred. A shirt bought on M-Pesa is routinely refunded in
cash because reversing a till payment is slower than the queue is patient, and
a credit note moves nothing at all. Counting every refund against the drawer
makes an M-Pesa reversal read as a shortfall; counting none makes a cash return
read as one. Both are wrong, so the method is recorded where the decision is
made — and the tests below are mostly about which of the three moves the
drawer.

── AND IT CLOSED A DOUBLE REFUND NOBODY HAD NOTICED ──────────────────────────

Reducing a customer's balance used to happen on ANY refund against a sale with
a credit payment, because the code had no way to ask how the money went back
and guessed from the sale. So a customer could be handed 1,500 in notes AND
have 1,500 come off what they owed. `test_cash_on_an_account_sale_does_not_also_
clear_the_debt` is that one.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from branches.services import drawer
from identity.models import PlatformAccount, TenantMembership
from organisations.models import BusinessOrganization

from . import services
from .models import Customer, Payment, Refund, Sale
from .tests import a_product, a_shop, stock


class Base(TestCase):
    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()
        self.product = a_product(self.org, price="100.00")
        stock(self.branch, self.product, "10")
        self.sale = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("4")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("400.00")}],
        )
        self.line = self.sale.items.get()

    def give_back(self, quantity="1", method=Payment.Method.CASH, **kwargs):
        return services.refund_sale(
            sale=self.sale,
            branch=self.branch,
            processed_by=self.staff,
            shift=self.shift,
            lines=[{"sale_item": self.line, "quantity": Decimal(quantity)}],
            reason="Wrong size",
            method=method,
            **kwargs,
        )


class MethodIsRequiredTests(Base):
    def test_a_refund_has_to_say_how_the_money_went_back(self):
        with self.assertRaises(services.SaleError):
            services.refund_sale(
                sale=self.sale,
                branch=self.branch,
                processed_by=self.staff,
                lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
                reason="Wrong size",
                method="",
            )

    def test_an_invented_method_is_refused(self):
        """
        Not merely stored and ignored. An unrecognised value would fall
        outside the drawer's `method=cash` filter and silently behave like a
        reversal that moved no cash.
        """
        with self.assertRaises(services.SaleError):
            services.refund_sale(
                sale=self.sale,
                branch=self.branch,
                processed_by=self.staff,
                lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
                reason="Wrong size",
                method="vouchers",
            )

    def test_the_method_is_kept_on_the_document(self):
        refund = self.give_back(method=Payment.Method.MPESA)
        refund.refresh_from_db()
        self.assertEqual(refund.method, Payment.Method.MPESA)


class DrawerTests(Base):
    """
    What the drawer should hold, before and after money goes back over the
    counter. Opening float is 1,000 and the sale took 400 in cash, so the
    starting expectation is 1,400 in every test here.
    """

    def expected(self) -> Decimal:
        return drawer(self.shift)["expected_cash"]

    def test_the_starting_point(self):
        self.assertEqual(self.expected(), Decimal("1400.00"))

    def test_cash_handed_back_lowers_what_the_drawer_should_hold(self):
        self.give_back(method=Payment.Method.CASH)
        self.assertEqual(self.expected(), Decimal("1300.00"))
        self.assertEqual(drawer(self.shift)["refunded_cash"], Decimal("100.00"))

    def test_an_mpesa_reversal_leaves_the_drawer_alone(self):
        """
        Nobody opened the drawer. Counting this would make the close read
        LONG, which is the same bug facing the other way and harder to spot —
        a drawer with too much in it looks like somebody being careful.
        """
        self.give_back(method=Payment.Method.MPESA)
        self.assertEqual(self.expected(), Decimal("1400.00"))
        self.assertEqual(drawer(self.shift)["refunded_cash"], Decimal("0.00"))

    def test_a_credit_note_leaves_the_drawer_alone(self):
        customer = Customer.objects.create(
            organization=self.org, full_name="A Debtor",
            phone_number="+254711000111",
        )
        Sale.objects.filter(pk=self.sale.pk).update(customer=customer)
        self.sale.refresh_from_db()
        self.give_back(method=Payment.Method.CREDIT)
        self.assertEqual(self.expected(), Decimal("1400.00"))

    def test_a_refund_recorded_before_the_method_existed_is_left_out(self):
        """
        ⚠ THE REASON THE COLUMN IS BLANKABLE.

        Every refund written before `Refund.method` shipped has no answer.
        Treating a blank as cash would restate the expected cash of shifts
        that were counted and closed months ago, turning a drawer that
        reconciled at the time into one that reads over by the value of its
        returns — retroactively, and with nobody able to explain why.
        """
        refund = self.give_back(method=Payment.Method.CASH)
        Refund.objects.filter(pk=refund.pk).update(method="")
        self.assertEqual(self.expected(), Decimal("1400.00"))

    def test_a_voided_refund_gives_the_money_back_to_the_drawer(self):
        """A reversal that was itself reversed moved no money."""
        refund = self.give_back(method=Payment.Method.CASH)
        self.assertEqual(self.expected(), Decimal("1300.00"))
        Refund.objects.filter(pk=refund.pk).update(status=Refund.Status.VOIDED)
        self.assertEqual(self.expected(), Decimal("1400.00"))

    def test_two_returns_both_come_off(self):
        self.give_back(quantity="1")
        self.give_back(quantity="2")
        self.assertEqual(self.expected(), Decimal("1100.00"))

    def test_a_return_at_another_till_does_not_touch_this_drawer(self):
        """
        `Refund.shift` is the shift that PROCESSED it, and the filter is on
        that and not on the sale's shift. A customer returning tomorrow what
        they bought today must not reach back into a closed drawer.
        """
        from branches.models import Register, RegisterShift

        other_register = Register.objects.create(
            branch=self.branch, name="Till 2", register_number="T2"
        )
        other_shift = RegisterShift.objects.create(
            register=other_register, operator=self.staff,
            opening_cash=Decimal("500.00"),
        )
        services.refund_sale(
            sale=self.sale,
            branch=self.branch,
            processed_by=self.staff,
            shift=other_shift,
            lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
            reason="Came back the next day",
            method=Payment.Method.CASH,
        )
        self.assertEqual(self.expected(), Decimal("1400.00"))
        self.assertEqual(
            drawer(other_shift)["expected_cash"], Decimal("400.00")
        )


class AccountTests(TestCase):
    """
    Money owed, and the double refund that used to be reachable.
    """

    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()
        self.product = a_product(self.org, price="100.00")
        stock(self.branch, self.product, "10")
        self.customer = Customer.objects.create(
            organization=self.org, full_name="A Debtor",
            phone_number="+254711000222",
        )
        self.sale = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            customer=self.customer,
            lines=[{"product": self.product, "quantity": Decimal("2")}],
            payments=[
                {"method": Payment.Method.CREDIT, "amount": Decimal("200.00")}
            ],
        )
        self.line = self.sale.items.get()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal("200.00"))

    def refund(self, method, quantity="1"):
        return services.refund_sale(
            sale=self.sale,
            branch=self.branch,
            processed_by=self.staff,
            shift=self.shift,
            lines=[{"sale_item": self.line, "quantity": Decimal(quantity)}],
            reason="Returned",
            method=method,
        )

    def balance(self) -> Decimal:
        self.customer.refresh_from_db()
        return self.customer.credit_balance

    def test_a_credit_note_clears_what_is_owed(self):
        self.refund(Payment.Method.CREDIT)
        self.assertEqual(self.balance(), Decimal("100.00"))

    def test_cash_on_an_account_sale_does_not_also_clear_the_debt(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE DOUBLE REFUND.

        Before `Refund.method`, the balance came down on any refund against
        a sale that had a credit payment — the code could not ask how the
        money went back, so it guessed from the sale and guessed the same way
        every time. Hand the customer 1,500 in notes and 1,500 came off what
        they owed as well.

        The drawer still drops, because notes genuinely left it. What does
        not happen is the debt moving too.
        ══════════════════════════════════════════════════════════════════
        """
        self.refund(Payment.Method.CASH)
        self.assertEqual(self.balance(), Decimal("200.00"))
        self.assertEqual(
            drawer(self.shift)["refunded_cash"], Decimal("100.00")
        )

    def test_a_credit_note_needs_an_account_to_put_it_on(self):
        """
        Without a customer there is no balance to reduce, so the money would
        simply have been written off while the paperwork said otherwise.
        """
        walk_in = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[
                {"method": Payment.Method.CASH, "amount": Decimal("100.00")}
            ],
        )
        with self.assertRaises(services.SaleError):
            services.refund_sale(
                sale=walk_in,
                branch=self.branch,
                processed_by=self.staff,
                lines=[
                    {"sale_item": walk_in.items.get(), "quantity": Decimal("1")}
                ],
                reason="Returned",
                method=Payment.Method.CREDIT,
            )


class RefundableQuantityTests(Base):
    """
    What the till is allowed to offer, line by line.

    The figure is derived from the refund items rather than stored, for the
    reason `refund_sale` derives it: a counter would be a second source of
    truth, and three partial returns of one shirt each must not be able to
    send four back. These assert the client is told the same number the
    server will enforce — a quantity box capped at what was SOLD lets a
    cashier type 3 against a line with 1 left and learn about it from a
    refusal, with a customer watching.
    """

    def serialised(self) -> dict:
        from .serializers import SaleSerializer

        self.sale.refresh_from_db()
        return SaleSerializer(self.sale).data["items"][0]

    def test_nothing_returned_yet(self):
        row = self.serialised()
        self.assertEqual(row["quantity_refunded"], "0.00")
        self.assertEqual(row["quantity_refundable"], "4.00")

    def test_a_partial_return_lowers_what_is_left(self):
        self.give_back(quantity="1")
        row = self.serialised()
        self.assertEqual(row["quantity_refunded"], "1.00")
        self.assertEqual(row["quantity_refundable"], "3.00")

    def test_returns_accumulate(self):
        self.give_back(quantity="1")
        self.give_back(quantity="2")
        row = self.serialised()
        self.assertEqual(row["quantity_refunded"], "3.00")
        self.assertEqual(row["quantity_refundable"], "1.00")

    def test_a_voided_refund_does_not_use_up_the_line(self):
        refund = self.give_back(quantity="2")
        Refund.objects.filter(pk=refund.pk).update(status=Refund.Status.VOIDED)
        row = self.serialised()
        self.assertEqual(row["quantity_refundable"], "4.00")

    def test_the_server_refuses_more_than_it_offered(self):
        self.give_back(quantity="3")
        with self.assertRaises(services.SaleError):
            self.give_back(quantity="2")


class WhoMayTakeAReturnTests(TestCase):
    """
    ═══════════════════════════════════════════════════════════════════════
    THE WHOLE REASON THE TILL ASKS FOR A MANAGER.

    `access.py` is explicit that a cashier gets neither `sales.void` nor
    `sales.refund`, and says why: voiding a sale and refunding one are how a
    till is emptied by the person standing at it, so they need a second
    person, and the cheapest version of a second person is a permission the
    first one does not hold.

    So "returns at the till" could not mean giving the cashier a refund
    button. The screen collects the return under the cashier's own session
    and then asks a manager to sign in at the terminal to authorise it —
    which works only because these two tests hold. If a cashier's token ever
    starts being accepted here, the manager prompt becomes decoration.
    ═══════════════════════════════════════════════════════════════════════
    """

    def setUp(self):
        from branches.models import staffAssignment
        from identity import services as identity_services

        self.org, self.branch, self.staff, _, self.shift = a_shop()
        self.product = a_product(self.org, price="100.00")
        stock(self.branch, self.product, "10")
        self.sale = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("2")}],
            payments=[
                {"method": Payment.Method.CASH, "amount": Decimal("200.00")}
            ],
        )
        self.line = self.sale.items.get()

        staffAssignment.objects.create(
            staff_member=self.staff, branch=self.branch, staff_assignment="CA"
        )
        credential = identity_services.issue_credential(
            staff=self.staff, username="jane", password="not-a-real-password"
        )
        _, self.cashier_token = identity_services.open_staff_session(credential)

        # A second person, which is the control. Their own staff row, their
        # own credential — a manager is not a cashier with a flag flipped.
        from organisations.models import OrganizationStaff

        self.boss = OrganizationStaff.objects.create(
            organization=self.org, full_name="A Manager",
            email="boss@shopa.co.ke", phone_number="+254700000099",
            address="Nairobi", id_number=4242,
        )
        staffAssignment.objects.create(
            staff_member=self.boss, branch=self.branch, staff_assignment="AM"
        )
        boss_credential = identity_services.issue_credential(
            staff=self.boss, username="boss", password="also-not-a-real-password"
        )
        _, self.boss_token = identity_services.open_staff_session(boss_credential)

    def post_return(self, token, processed_by):
        return self.client.post(
            f"/sls/sales/{self.sale.pk}/refund/",
            {
                "branch": self.branch.pk,
                "processed_by": processed_by.pk,
                "shift": self.shift.pk,
                "lines": [
                    {"sale_item": self.line.pk, "quantity": "1", "restock": True}
                ],
                "reason": "Wrong size",
                "method": Payment.Method.CASH,
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_a_cashier_cannot_take_a_return(self):
        response = self.post_return(self.cashier_token, self.staff)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Refund.objects.count(), 0)

    def test_a_manager_can(self):
        response = self.post_return(self.boss_token, self.boss)
        self.assertEqual(response.status_code, 201)
        refund = Refund.objects.get()
        self.assertEqual(refund.processed_by_id, self.boss.pk)
        self.assertEqual(refund.method, Payment.Method.CASH)
        self.assertEqual(refund.shift_id, self.shift.pk)

    def test_a_manager_cannot_record_it_against_the_cashier(self):
        """
        `processed_by` is the only record of who authorised money leaving the
        drawer, and the server pins it to whoever is signed in. Without this
        the manager prompt would collect a real authorisation and then file
        it under somebody else's name.
        """
        response = self.post_return(self.boss_token, self.staff)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Refund.objects.count(), 0)

    def test_the_method_is_required_through_the_api_too(self):
        response = self.client.post(
            f"/sls/sales/{self.sale.pk}/refund/",
            {
                "branch": self.branch.pk,
                "processed_by": self.boss.pk,
                "shift": self.shift.pk,
                "lines": [{"sale_item": self.line.pk, "quantity": "1"}],
                "reason": "Wrong size",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.boss_token}",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("method", response.json())

    def test_the_three_requests_the_till_actually_makes(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE BORROWED CREDENTIAL IS NOT LEFT LIVE ON A SHARED TERMINAL.

        Return.tsx signs a manager in, posts the refund with that token, and
        signs out — and the banner there claims the terminal is not left
        holding a manager session for whoever touches it next. That is a
        claim about `/auth/staff/sign-out`, so it is asserted here rather
        than only described there.

        The last line is the one that matters: the same token that was good
        enough to authorise a refund a moment earlier is refused afterwards.
        ══════════════════════════════════════════════════════════════════
        """
        signed_in = self.client.post(
            "/auth/staff/sign-in",
            {
                "organization": self.org.pk,
                "username": "boss",
                "password": "also-not-a-real-password",
            },
            content_type="application/json",
        )
        self.assertEqual(signed_in.status_code, 201)
        token = signed_in.json()["token"]

        self.assertEqual(self.post_return(token, self.boss).status_code, 201)

        signed_out = self.client.post(
            "/auth/staff/sign-out",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(signed_out.status_code, 204)

        # Dead, immediately. Not merely unused.
        after = self.client.get(
            "/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertIn(after.status_code, (401, 403))

    def test_the_drawer_follows_a_return_taken_through_the_api(self):
        """
        End to end, because the drawer is the thing that was wrong. 1,000
        float plus 200 taken, less 100 handed back.
        """
        self.assertEqual(drawer(self.shift)["expected_cash"], Decimal("1200.00"))
        self.assertEqual(self.post_return(self.boss_token, self.boss).status_code, 201)
        self.assertEqual(drawer(self.shift)["expected_cash"], Decimal("1100.00"))


class SaleLookupTests(TestCase):
    """
    `?number=` — turning the number on a receipt into a sale.

    A customer at the counter has a receipt in their hand and nothing else
    that identifies the sale. The till cannot list a day's trading and
    paginate through it while somebody waits.
    """

    def setUp(self):
        self.a_org, self.a_branch, self.a_staff, _, self.a_shift = a_shop("Shop A")
        self.b_org, self.b_branch, self.b_staff, _, self.b_shift = a_shop("Shop B")
        self.a_product = a_product(self.a_org, name="A Milk")
        self.b_product = a_product(self.b_org, name="B Milk")
        stock(self.a_branch, self.a_product, "10")
        stock(self.b_branch, self.b_product, "10")

        self.a_sale = services.checkout(
            shift=self.a_shift,
            cashier=self.a_staff,
            lines=[{"product": self.a_product, "quantity": Decimal("1")}],
            payments=[
                {"method": Payment.Method.CASH, "amount": Decimal("100.00")}
            ],
        )
        self.b_sale = services.checkout(
            shift=self.b_shift,
            cashier=self.b_staff,
            lines=[{"product": self.b_product, "quantity": Decimal("1")}],
            payments=[
                {"method": Payment.Method.CASH, "amount": Decimal("100.00")}
            ],
        )

        self.account = PlatformAccount.objects.create(
            genmars_account_id=9001, email="owner@a.co.ke", full_name="A Owner"
        )
        TenantMembership.objects.create(
            account=self.account, organization=self.a_org
        )

    def sign_in(self):
        from identity.authentication import SUBSCRIBER_SESSION_KEY

        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.account.pk
        session.save()

    def rows(self, query: str) -> list:
        response = self.client.get(f"/sls/sales/{query}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        return body["results"] if isinstance(body, dict) else body

    def test_a_number_finds_its_sale(self):
        self.sign_in()
        rows = self.rows(f"?number={self.a_sale.number}")
        self.assertEqual([row["id"] for row in rows], [self.a_sale.pk])

    def test_a_number_both_shops_used_answers_only_with_your_own(self):
        """
        ⚠ NUMBERS COLLIDE ACROSS TENANTS, BY DESIGN.

        `number` is unique per organisation, not globally — both shops here
        number their first sale 1000, which is the realistic case and not a
        contrived one. So this is the test that matters: asked for 1000,
        Shop A is told about Shop A's sale and never learns that Shop B has
        one too.

        It is also why the filter narrows an already-scoped queryset instead
        of looking a number up and then checking it. There is nothing to
        check — a number outside the scope matches nothing, and no refusal
        has to be written to make that true. §8 arranged so that obeying it
        needs no extra code.
        """
        self.sign_in()
        self.assertEqual(self.a_sale.number, self.b_sale.number)
        rows = self.rows(f"?number={self.b_sale.number}")
        self.assertEqual([row["id"] for row in rows], [self.a_sale.pk])
        self.assertNotIn(self.b_sale.pk, [row["id"] for row in rows])

    def test_a_number_nobody_used_finds_nothing_rather_than_refusing(self):
        """
        An empty list, not a 403. A refusal would tell the caller the
        difference between "no such sale" and "not yours", which is the
        enumeration oracle a 404 exists to avoid everywhere else here.
        """
        self.sign_in()
        self.assertEqual(self.rows("?number=999999"), [])

    def test_a_number_that_is_not_a_number_finds_nothing(self):
        """
        A cashier typing a receipt code with a letter in it should be told
        nothing was found, which is true, rather than shown a validation
        error about a field they did not know they were filling in.
        """
        self.sign_in()
        self.assertEqual(self.rows("?number=T1-0042"), [])

    def test_no_number_still_lists(self):
        self.sign_in()
        self.assertEqual(len(self.rows("")), 1)
