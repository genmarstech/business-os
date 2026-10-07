"""What a feed looks like on the wire."""

from __future__ import annotations

from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """
    One entry.

    ⚠ `permission` IS NOT HERE. It is the audience rule, and publishing it
      would tell a cashier which authority they hold — information they have no
      use for and which describes other people's access as much as their own.
      The server has already decided they may see the row; how it decided is
      not part of the answer.

    `actor_*` are not here either, for a plainer reason: "Jane closed the till
    short" is in `subject` already, written when it was raised, and exposing
    the ids would let a client build a who-did-what report out of a feed, which
    is what ActivityLog and the ops reports are for.
    """

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    branch_name = serializers.CharField(
        source="branch.branch_name", read_only=True, default=""
    )

    class Meta:
        model = Notification
        fields = [
            "id",
            "kind",
            "kind_label",
            "urgency",
            "subject",
            "body",
            "path",
            "branch",
            "branch_name",
            "created_at",
        ]
        read_only_fields = fields
