"""
What a thing costs.

The tests that matter here are the ones where the shelf edge and the till
could come to disagree, because that disagreement is an argument with a
customer and the shop has no way to find out who is right.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from catalog import pricing
from catalog.models import PriceList

from .factories import a_price_list, a_product, a_shop


class ResolutionTests(TestCase):
    def setUp(self):
        self.org, (self.west, self.karen) = a_shop(
            "Grocers", branches=("Westlands", "Karen")
        )
        self.milk = a_product(self.org, name="Milk", price="100.00")
        self.bread = a_product(self.org, name="Bread", price="60.00")
        self.today = timezone.localdate()

    def price(self, product, branch=None, on=None):
        amount, _ = pricing.price_for(
            product, branch_id=branch.pk if branch else None, on=on
        )
        return amount

    def test_with_no_lists_the_products_own_price_stands(self):
        """
        The overwhelming majority of shops, and the case that must not get
        slower or cleverer because a feature exists for the rest.
        """
        self.assertEqual(self.price(self.milk, self.west), Decimal("100.00"))

    def test_a_list_overrides_the_products_price(self):
        a_price_list(self.org, name="Promo", precedence=10,
                     prices=[(self.milk, "80.00")])
        self.assertEqual(self.price(self.milk, self.west), Decimal("80.00"))

    def test_a_list_is_silent_about_what_it_does_not_name(self):
        """
        A promotion listing three items must not blank out the prices of
        everything it does not mention. The walk takes the first list
        holding an ENTRY, not the first list that applies.
        """
        a_price_list(self.org, name="Promo", precedence=10,
                     prices=[(self.milk, "80.00")])
        self.assertEqual(self.price(self.bread, self.west), Decimal("60.00"))

    def test_the_higher_precedence_list_wins(self):
        a_price_list(self.org, name="Branch prices", precedence=10,
                     prices=[(self.milk, "90.00")])
        a_price_list(self.org, name="Promotion", precedence=50,
                     prices=[(self.milk, "75.00")])
        self.assertEqual(self.price(self.milk, self.west), Decimal("75.00"))

    def test_a_weaker_list_still_prices_what_the_stronger_one_omits(self):
        a_price_list(self.org, name="Branch prices", precedence=10,
                     prices=[(self.milk, "90.00"), (self.bread, "55.00")])
        a_price_list(self.org, name="Promotion", precedence=50,
                     prices=[(self.milk, "75.00")])
        self.assertEqual(self.price(self.milk, self.west), Decimal("75.00"))
        self.assertEqual(self.price(self.bread, self.west), Decimal("55.00"))

    def test_two_lists_cannot_share_a_precedence(self):
        """
        ── THE WHOLE REASON THE CONSTRAINT EXISTS ─────────────────────────
        A tie would be broken by whatever order the database felt like
        returning rows in, so a till and a receipt printed a second apart
        could disagree and the shop would have no way to find out why. It is
        refused at write time instead, once, by whoever makes the list.
        """
        a_price_list(self.org, name="One", precedence=10)
        with self.assertRaises(Exception):
            a_price_list(self.org, name="Two", precedence=10)

    def test_another_shop_may_use_the_same_precedence(self):
        """The constraint is per organisation, like every other one here."""
        other, _ = a_shop("Shop B")
        a_price_list(self.org, name="One", precedence=10)
        a_price_list(other, name="One", precedence=10)

    # ── where ───────────────────────────────────────────────────────────────

    def test_a_list_with_no_branches_applies_everywhere(self):
        a_price_list(self.org, name="Everywhere", precedence=10,
                     prices=[(self.milk, "80.00")])
        self.assertEqual(self.price(self.milk, self.west), Decimal("80.00"))
        self.assertEqual(self.price(self.milk, self.karen), Decimal("80.00"))

    def test_a_list_named_at_one_branch_does_not_reach_the_other(self):
        a_price_list(self.org, name="Westlands", precedence=10,
                     prices=[(self.milk, "120.00")], branches=[self.west])
        self.assertEqual(self.price(self.milk, self.west), Decimal("120.00"))
        self.assertEqual(self.price(self.milk, self.karen), Decimal("100.00"))

    def test_a_list_can_name_several_branches_but_not_all(self):
        """
        The case a nullable branch column could not express, and the reason
        there is a join table rather than one foreign key.
        """
        _, (third,) = a_shop("Grocers Two", branches=("Kisumu",))
        a_price_list(self.org, name="Two of three", precedence=10,
                     prices=[(self.milk, "88.00")],
                     branches=[self.west, self.karen])
        self.assertEqual(self.price(self.milk, self.west), Decimal("88.00"))
        self.assertEqual(self.price(self.milk, self.karen), Decimal("88.00"))

    def test_with_no_branch_in_hand_only_organisation_wide_lists_apply(self):
        """
        Guessing at a branch's own prices is how one shop's promotion gets
        shown at another.
        """
        a_price_list(self.org, name="Westlands", precedence=50,
                     prices=[(self.milk, "120.00")], branches=[self.west])
        a_price_list(self.org, name="Everywhere", precedence=10,
                     prices=[(self.milk, "95.00")])
        self.assertEqual(self.price(self.milk), Decimal("95.00"))

    # ── when ────────────────────────────────────────────────────────────────

    def test_a_window_includes_both_of_its_ends(self):
        """
        "Ends on the 30th" and a price that reverted on the morning of the
        30th is an argument with a customer holding a flyer.
        """
        a_price_list(
            self.org, name="Fortnight", precedence=10,
            prices=[(self.milk, "80.00")],
            starts_on=self.today, ends_on=self.today + timedelta(days=13),
        )
        self.assertEqual(self.price(self.milk, self.west, self.today),
                         Decimal("80.00"))
        self.assertEqual(
            self.price(self.milk, self.west, self.today + timedelta(days=13)),
            Decimal("80.00"),
        )

    def test_a_promotion_that_has_not_started_prices_nothing(self):
        a_price_list(
            self.org, name="Next week", precedence=10,
            prices=[(self.milk, "80.00")],
            starts_on=self.today + timedelta(days=7),
        )
        self.assertEqual(self.price(self.milk, self.west), Decimal("100.00"))

    def test_a_promotion_that_has_ended_prices_nothing(self):
        a_price_list(
            self.org, name="Last week", precedence=10,
            prices=[(self.milk, "80.00")],
            ends_on=self.today - timedelta(days=1),
        )
        self.assertEqual(self.price(self.milk, self.west), Decimal("100.00"))

    def test_a_deactivated_list_prices_nothing(self):
        a_price_list(self.org, name="Off", precedence=10,
                     prices=[(self.milk, "80.00")], is_active=False)
        self.assertEqual(self.price(self.milk, self.west), Decimal("100.00"))

    # ── the awkward values ──────────────────────────────────────────────────

    def test_a_price_of_zero_means_zero(self):
        """
        A giveaway is a real thing a shop does. Encoding "no override" as
        0.00 would make the two indistinguishable, so the ROW is the
        override and its value is taken at face value.
        """
        a_price_list(self.org, name="Free milk", precedence=10,
                     prices=[(self.milk, "0.00")])
        self.assertEqual(self.price(self.milk, self.west), Decimal("0.00"))

    def test_a_negative_price_is_refused(self):
        """A price below nothing pays the customer to take the stock."""
        with self.assertRaises(Exception):
            a_price_list(self.org, name="Nonsense", precedence=10,
                         prices=[(self.milk, "-5.00")])

    def test_a_backwards_window_is_refused(self):
        """
        Stored, it looks like a configured promotion that silently never
        happens, and the shop blames the till.
        """
        with self.assertRaises(Exception):
            a_price_list(
                self.org, name="Backwards", precedence=10,
                starts_on=self.today, ends_on=self.today - timedelta(days=1),
            )

    # ── isolation ───────────────────────────────────────────────────────────

    def test_another_shops_list_does_not_price_this_shops_product(self):
        other, _ = a_shop("Shop B")
        a_price_list(other, name="Theirs", precedence=10,
                     prices=[(a_product(other, name="Their Milk"), "10.00")])
        self.assertEqual(self.price(self.milk, self.west), Decimal("100.00"))

    def test_resolving_across_two_organisations_is_a_programming_error(self):
        """
        Returning a quiet answer would mean one shop's list could be asked
        to price another's product, which is the shape of a leak.
        """
        other, _ = a_shop("Shop B")
        with self.assertRaises(ValueError):
            pricing.prices_for([self.milk, a_product(other, name="Theirs")])

    # ── the shape of the query ──────────────────────────────────────────────

    def test_pricing_a_whole_catalogue_costs_a_fixed_number_of_queries(self):
        """
        ── THE N+1 THIS MODULE IS BUILT AROUND ────────────────────────────
        A till opens with the whole catalogue on screen. Resolving one
        product at a time would be two queries per product — hundreds, on a
        connection in a shop. The count must not move with the catalogue.
        """
        a_price_list(self.org, name="Promo", precedence=10,
                     prices=[(self.milk, "80.00")])
        many = [
            a_product(self.org, name=f"Thing {n}", price="10.00")
            for n in range(40)
        ]
        with self.assertNumQueries(2):
            pricing.prices_for([self.milk, self.bread, *many],
                               branch_id=self.west.pk)

    def test_an_empty_catalogue_costs_no_queries_at_all(self):
        with self.assertNumQueries(0):
            self.assertEqual(pricing.prices_for([]), {})
