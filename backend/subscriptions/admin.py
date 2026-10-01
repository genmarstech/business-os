"""
Genmars' side of a subscription.

══════════════════════════════════════════════════════════════════════════════
EVERY CHANGE GOES THROUGH services.py, INCLUDING THE ONES MADE FROM HERE.

The model is emphatic that it does, and the API enforces it by making every
field on `SubscriptionSerializer` read-only: a PATCH that could set
`paid_until` would be a payment with no money behind it and no record of who
decided otherwise.

The admin did not enforce it. Every field was editable, so a support person
could move `paid_until` eighteen months into the future by typing in a box,
and the append-only log — the only account of what was agreed with a customer
and by whom — would say nothing happened. The one surface that bypassed the
rule was the one Genmars actually uses.

So the fields are read-only here too, and the changes are actions that call
the services. Each writes a SubscriptionEvent naming the person who did it.
══════════════════════════════════════════════════════════════════════════════

── AND THIS IS WHAT MAKES THE PRODUCT CHARGEABLE TODAY ───────────────────────

`services.extend` existed and nothing called it. It is documented as "the
record of a decision, not the making of one" — called by something that has
already seen the money — and there was no something. A customer could pay by
bank transfer, by M-Pesa to the company till, in cash, and Genmars had no way
to record it: the trial lapsed and the subscription suspended regardless.

Taking the money is still not this application's job and deliberately never
will be. Recording that it arrived is, and now it can.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.template.response import TemplateResponse
from django.utils import timezone

from . import services
from .models import Plan, Subscription, SubscriptionEvent


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """
    What Genmars offers.

    The table ships empty on purpose — see the banner on the model. A price
    invented by whoever wrote the code is a price the company never agreed
    to, shown to a customer (Charter 04 §IV). This is where the real ones
    are entered.

    ── A WITHDRAWN PLAN IS NOT A DELETED ONE ──────────────────────────────
    `is_offered` takes a plan off the list new subscribers can choose and
    leaves it working for everybody already on it. Deleting one is refused
    by the database anyway — Subscription.plan is PROTECT — and moving
    somebody off terms they agreed to is a commercial decision, not a
    cleanup.
    """

    list_display = ["name", "code", "monthly_price", "branch_limit",
                    "register_limit", "staff_limit", "subscribers", "is_offered"]
    list_filter = ["is_offered"]
    search_fields = ["name", "code"]
    ordering = ["sort_order", "monthly_price"]

    def get_queryset(self, request):
        from django.db.models import Count

        return super().get_queryset(request).annotate(_subs=Count("subscriptions"))

    @admin.display(description="on this plan", ordering="_subs")
    def subscribers(self, obj) -> int:
        return obj._subs

    def has_delete_permission(self, request, obj=None):
        return False


class ExtendForm(forms.Form):
    until = forms.DateField(
        label="Paid up to and including",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=(
            "The last day the money covers. Inclusive — a month paid on the "
            "1st usually runs to the 30th or 31st, not to the 1st."
        ),
    )
    note = forms.CharField(
        required=False,
        max_length=200,
        help_text="How it arrived, if it is worth recording. Goes in the log.",
    )


class PlanForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.all())


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["organization", "state", "plan", "covered_until",
                    "days_left", "cancelled_on"]
    list_filter = ["plan"]
    search_fields = ["organization__name", "organization__org_number"]
    ordering = ["organization__name"]
    actions = ["record_payment", "move_to_plan", "cancel", "reinstate"]

    # See the banner. Nothing here is typed into directly.
    readonly_fields = [
        "organization", "plan", "started_on", "trial_ends_on", "paid_until",
        "grace_days", "cancelled_on", "cancellation_reason", "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        """
        A subscription is opened by `services.open_trial`, inside the same
        transaction that creates the business. One made by hand here would
        have no trial-started event and no started_on that means anything.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        Deleting one deletes the record of what a customer agreed to and
        paid. Ending an arrangement is Cancel, which is reversible and
        leaves the history.
        """
        return False

    @admin.display(description="state")
    def state(self, obj) -> str:
        """Derived, never stored — see the banner on the model."""
        return obj.get_state_display()

    @admin.display(description="days left")
    def days_left(self, obj):
        return obj.days_left

    # ── the actions ─────────────────────────────────────────────────────────

    def _with_form(self, request, queryset, *, form_class, action, title, lede,
                   submit_label, apply):
        """
        An action that needs a value, shown as an intermediate page.

        The selected subscriptions are listed on it with their current state,
        because "paid to the 30th" typed against the wrong shop is a mistake
        nothing downstream will catch.
        """
        if request.POST.get("apply"):
            form = form_class(request.POST)
            if form.is_valid():
                done, refused = 0, []
                for subscription in queryset:
                    try:
                        apply(subscription, form.cleaned_data, request)
                        done += 1
                    except ValidationError as error:
                        refused.append(
                            f"{subscription.organization.name}: "
                            f"{'; '.join(error.messages)}"
                        )
                if done:
                    self.message_user(
                        request, f"Done for {done}.", messages.SUCCESS
                    )
                for problem in refused:
                    self.message_user(request, problem, messages.WARNING)
                return None
        else:
            form = form_class()

        return TemplateResponse(
            request,
            "admin/subscriptions/action_form.html",
            {
                **self.admin_site.each_context(request),
                "title": title,
                "lede": lede,
                "form": form,
                "subscriptions": queryset,
                "action": action,
                "submit_label": submit_label,
            },
        )

    @admin.action(description="Record a payment")
    def record_payment(self, request, queryset):
        """
        ── THE ONE THAT MAKES THE PRODUCT CHARGEABLE ──────────────────────
        Genmars takes the money however it takes it — bank transfer, the
        company till, cash — and records here that it arrived and what it
        covers. `services.extend` refuses a date earlier than what is
        already paid for, because that is almost always a bug in whoever
        worked the date out and applying it would take back time a customer
        has bought.
        """
        return self._with_form(
            request, queryset,
            form_class=ExtendForm,
            action="record_payment",
            title="Record a payment",
            lede=(
                "This records that money has arrived and what period it "
                "covers. It does not take a payment — nothing in this "
                "application can."
            ),
            submit_label="Record it",
            apply=lambda sub, data, req: services.extend(
                sub, until=data["until"], note=data["note"]
            ),
        )

    @admin.action(description="Move to a plan")
    def move_to_plan(self, request, queryset):
        """
        Choosing a plan says which terms apply; paying says until when. They
        arrive separately on purpose — a tenant can agree terms on the 1st
        and have the money land on the 5th.
        """
        return self._with_form(
            request, queryset,
            form_class=PlanForm,
            action="move_to_plan",
            title="Move to a plan",
            lede=(
                "Sets which terms apply, and with them the ceilings on "
                "branches, tills and staff. It does not extend anything."
            ),
            submit_label="Move them",
            apply=lambda sub, data, req: services.choose_plan(sub, data["plan"]),
        )

    @admin.action(description="Cancel")
    def cancel(self, request, queryset):
        """
        Takes effect at the end of what has been paid for, not today — a
        customer who cancels on the 3rd having paid to the 30th keeps the
        month they bought.
        """
        done = 0
        for subscription in queryset:
            try:
                services.cancel(subscription, reason="Cancelled by Genmars")
                done += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{subscription.organization.name}: {'; '.join(error.messages)}",
                    messages.WARNING,
                )
        if done:
            self.message_user(request, f"Cancelled {done}.", messages.SUCCESS)

    @admin.action(description="Reinstate")
    def reinstate(self, request, queryset):
        done = 0
        for subscription in queryset:
            try:
                services.reinstate(subscription)
                done += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{subscription.organization.name}: {'; '.join(error.messages)}",
                    messages.WARNING,
                )
        if done:
            self.message_user(request, f"Reinstated {done}.", messages.SUCCESS)


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    """
    Read-only, because the log is append-only. A correction is another entry.
    """

    list_display = ["at", "subscription", "kind", "actor_account", "note"]
    list_filter = ["kind"]
    search_fields = ["subscription__organization__name"]
    ordering = ["-at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
