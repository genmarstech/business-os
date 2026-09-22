from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import render
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from . import services
from .serializers import RegisterSerializer, RegisterShiftSerializer, BranchesSerializer, StaffAssignmentSerializer
from .models import Branches, Register, RegisterShift, staffAssignment
from identity import access
from identity.scoping import TenantScoped


# Create your views here.

@api_view(['GET'])
def greeting(request):
    message = 'Welcome to the branches api'

    return Response({'message': message})

class BranchesViewSets(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "organization_id"
    # "id", because this IS the branch table. §5: a branch manager "should see
    # only the data and actions permitted for that branch", and a till has no
    # business enumerating the shop's other locations.
    branch_path = "id"

    # Reading the branch list is how a till knows where it is; creating and
    # renaming branches is an organisation-level act (§4).
    default_permission = access.BRANCH_MANAGE
    permissions = {
        "list": access.CATALOG_VIEW,
        "retrieve": access.CATALOG_VIEW,
    }
    queryset = Branches.objects.all()
    serializer_class = BranchesSerializer


class RegisterViewSets(TenantScoped, viewsets.ModelViewSet):
    """A register reaches its organisation through its branch."""

    tenant_path = "branch__organization_id"
    branch_path = "branch_id"
    default_permission = access.REGISTER_MANAGE
    permissions = {
        "list": access.SALES_VIEW,
        "retrieve": access.SALES_VIEW,
    }
    queryset = Register.objects.select_related('branch').all()
    serializer_class = RegisterSerializer

class RegisterShiftViewSets(TenantScoped, viewsets.ModelViewSet):
    """Two hops: shift -> register -> branch -> organisation."""

    tenant_path = "register__branch__organization_id"

    # Opening a shift is a cashier's own act; closing one is where the
    # drawer is reconciled, so it sits with the manager (module 7).
    branch_path = "register__branch_id"
    default_permission = access.SHIFT_OPEN
    # ══════════════════════════════════════════════════════════════════════
    # EVERY ACTION IS NAMED, INCLUDING THE CUSTOM ONES.
    #
    # An @action that is not in this map silently inherits
    # `default_permission` — and this viewset's default is SHIFT_OPEN, which
    # every cashier holds. `close` was added without a line here and a cashier
    # counted their own drawer 300 short, closed it, and got a 200.
    #
    # That is the fail-open direction, on the viewset where it costs the most.
    # A new action needs a line here in the same commit that adds it.
    # ══════════════════════════════════════════════════════════════════════
    permissions = {
        "list": access.SALES_VIEW,
        "retrieve": access.SALES_VIEW,
        "destroy": access.SHIFT_CLOSE,
        "update": access.SHIFT_CLOSE,
        "partial_update": access.SHIFT_CLOSE,
        # Seeing what the drawer should hold is not closing against it — the
        # cashier holding the notes is usually the one counting.
        "drawer": access.SALES_VIEW,
        "close": access.SHIFT_CLOSE,
    }
    queryset = RegisterShift.objects.select_related('register__branch').all()
    serializer_class = RegisterShiftSerializer

    @action(detail=True, methods=["get"])
    def drawer(self, request, pk=None):
        """
        What this till should be holding, so somebody can count against it.

        Readable by anyone who may see sales — the cashier standing at the
        drawer is usually the one counting, and they hold SALES_VIEW but not
        SHIFT_CLOSE. Seeing the figure is not closing against it.
        """
        shift = self.get_object()
        return Response(_money(services.drawer(shift)))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """
        End the shift against a counted drawer.

        ⚠ THE COUNT IS REQUIRED. An optional one would be left blank on the
          busy evenings that are exactly when a drawer goes short.
        """
        shift = self.get_object()

        if "counted_cash" not in request.data:
            return Response(
                {"counted_cash": "Count the drawer and enter what is in it."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            closed = services.close_shift(
                shift=shift, counted_cash=request.data.get("counted_cash")
            )
        except DjangoValidationError as error:
            detail = (
                error.message_dict
                if hasattr(error, "message_dict")
                else {"detail": error.messages}
            )
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "shift": self.get_serializer(closed).data,
                "drawer": _money(services.drawer(closed)),
            }
        )


def _money(figures: dict) -> dict:
    """
    Decimals as strings.

    DRF's JSON encoder does float(obj), so 1234.55 goes out fine and 19.99
    leaves as 19.989999999999998. A variance is the one figure on this screen
    somebody will argue about, and it has to be exact — sales/views.py carries
    the same conversion and the same note.
    """
    return {
        key: (str(value) if value is not None and not isinstance(value, (int, str)) else value)
        for key, value in figures.items()
    }


class StaffAssignmentViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "branch__organization_id"

    # Who works where, and as what. This is the table that grants every
    # operational permission in identity/access.py, so writing to it is
    # held at the narrowest permission there is.
    branch_path = "branch_id"
    default_permission = access.STAFF_MANAGE
    permissions = {
        "list": access.STAFF_MANAGE,
        "retrieve": access.STAFF_MANAGE,
    }
    queryset = staffAssignment.objects.select_related('branch').all()
    serializer_class = StaffAssignmentSerializer

    def perform_create(self, serializer):
        # No scope check needed here: TenantScoped.create() has already run it
        # before this method is reached, which is the whole reason the guard
        # lives there rather than in perform_create.
        staff_member = serializer.validated_data['staff_member']

        # Somebody moving to a new role stops holding the old one. Filtered by
        # staff member, which is itself tenant-bound and has just been checked.
        staffAssignment.objects.filter(
            staff_member=staff_member,
            is_active=True
        ).update(is_active=False)

        serializer.save(is_active=True)