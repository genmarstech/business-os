"""
Genmars' own view of the platform.

══════════════════════════════════════════════════════════════════════════════
THIS IS THE ONLY PLACE IN THE PRODUCT THAT SEES ACROSS TENANTS.

Everything else is scoped, deliberately and in one file: `identity/scoping.py`
confines every queryset to the caller's organisations, and the rest of the
codebase is written so that reaching past it takes effort. That is the
property the whole application is built on.

So a cross-tenant view is a considered exception, not a convenience, and it
lives here — behind `/admin/`, which no tenant principal can reach at all:
a PlatformAccount is not a Django user and a StaffCredential is not a Django
user. The only accounts that open this are the ones `createsuperuser` makes,
which are Genmars'.

It is also why that door now wants a second factor. See identity/totp.py.
══════════════════════════════════════════════════════════════════════════════

── IT IS READ-ONLY, WITH ONE EXCEPTION, AND THE LINE IS WHO OWNS THE FIELD ───

Genmars can SEE a tenant's name, sector and size. Genmars cannot change them
from here: those are the shop's own facts, the shop edits them under Business
details, and a support person quietly renaming somebody's business is a change
with no trail on the side that cares about it.

The exception is `genmars_organisation_id`, which is not the shop's fact at
all — it records which Genmars client to invoice, has no meaning inside the
tenant, and is the one thing on this row that only Genmars can know.
"""

from django.contrib import admin
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import BusinessOrganization, OrganizationStaff


@admin.register(BusinessOrganization)
class BusinessOrganizationAdmin(admin.ModelAdmin):
    """The roster: who is on the platform, and how they are getting on."""

    list_display = [
        "org_number",
        "name",
        "sector",
        "subscription",
        "branches",
        "tills",
        "working_staff",
        "last_traded",
        "created_at",
    ]
    list_filter = ["sector", "staff_size"]
    search_fields = ["name", "org_number"]
    ordering = ["-created_at"]
    list_per_page = 50

    # See the banner: the shop's own facts are the shop's to change.
    readonly_fields = [
        "name", "org_number", "sector", "staff_size", "created_at", "updated_at",
    ]
    fields = readonly_fields + ["genmars_organisation_id"]

    def has_add_permission(self, request):
        """
        A business is created by its owner signing up, in a transaction that
        also makes them its owner and opens their trial. Adding a bare row
        here would produce a tenant nobody can reach and no subscription.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        Deleting a tenant deletes a real business's sales history, and most
        of what points at it is PROTECT so it would half-fail anyway. If a
        customer leaves, their subscription is cancelled; the records stay.
        """
        return False

    # ── ONE QUERY, NOT ONE PER ROW ──────────────────────────────────────────
    #
    # Every column below is a count or an aggregate. Computed per row they
    # would be five queries times fifty rows on a page nobody would then
    # open. Annotated here they are one.
    #
    # `distinct=True` on each Count is load-bearing: several joins in one
    # query multiply each other's rows, and without it a shop with three
    # branches and four staff reports twelve of each.
    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("subscription", "subscription__plan")
            .annotate(
                _branches=Count(
                    "branches", filter=Q(branches__is_active=True), distinct=True
                ),
                _tills=Count(
                    "branches__registers",
                    filter=Q(branches__registers__is_active=True),
                    distinct=True,
                ),
                _staff=Count(
                    "staff__assignments__staff_member",
                    filter=Q(staff__assignments__is_active=True),
                    distinct=True,
                ),
                _last_sale=Max("sales__completed_at"),
            )
        )

    @admin.display(description="subscription", ordering="subscription__plan")
    def subscription(self, obj) -> str:
        """
        The derived state, not a stored one — see subscriptions/models.py on
        why there is no status column. A tenant with no row reads as
        "unrecorded" rather than as lapsed, which is what the entitlement
        layer assumes too.
        """
        row = getattr(obj, "subscription", None)
        if row is None:
            return "— unrecorded"
        plan = row.plan.name if row.plan else "no plan"
        return f"{row.get_state_display()} · {plan}"

    @admin.display(description="branches", ordering="_branches")
    def branches(self, obj) -> int:
        return obj._branches

    @admin.display(description="tills", ordering="_tills")
    def tills(self, obj) -> int:
        return obj._tills

    @admin.display(description="staff", ordering="_staff")
    def working_staff(self, obj) -> int:
        """
        People with a live assignment, not rows ever written — the same
        count `subscriptions.entitlement` enforces a plan limit against, so
        this page and that refusal cannot disagree about the number.
        """
        return obj._staff

    @admin.display(description="last traded", ordering="_last_sale")
    def last_traded(self, obj) -> str:
        """
        ── THE COLUMN THAT ACTUALLY TELLS YOU SOMETHING ────────────────────
        A shop that has not rung up a sale in a fortnight is either on
        holiday or has quietly stopped using the product, and the second is
        the thing worth knowing before the renewal rather than after it.
        """
        when = obj._last_sale
        if when is None:
            return "never"
        days = (timezone.now() - when).days
        if days == 0:
            return "today"
        return f"{days} day{'' if days == 1 else 's'} ago"


@admin.register(OrganizationStaff)
class OrganizationStaffAdmin(admin.ModelAdmin):
    """
    Other companies' employees.

    ⚠ THESE ARE NOT GENMARS' PEOPLE, AND THE ROWS CARRY THEIR PERSONAL DATA.
    National ID and KRA PIN belong to a customer's staff, which makes Genmars
    a processor under the Data Protection Act. Read-only here: there is no
    support reason to edit somebody else's employee record, and every reason
    not to be able to.
    """

    list_display = ["full_name", "organization", "email", "staff_number"]
    list_filter = ["organization"]
    search_fields = ["full_name", "email", "staff_number"]
    ordering = ["organization", "full_name"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
