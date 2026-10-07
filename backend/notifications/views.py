"""
Reading the feed, and marking it read.

Three endpoints, and no way to create a notification through the API.

══════════════════════════════════════════════════════════════════════════════
THERE IS DELIBERATELY NO POST THAT RAISES ONE.

A notification is a statement by the system that something happened. An
endpoint that let a client assert one would let anybody holding any token
manufacture "M-Pesa payment confirmed" into a manager's feed — and the whole
value of the thing is that its entries are true. They are raised by the
services that perform the act, inside the same transaction, or not at all.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from identity.permissions import IsKnownPrincipal

from . import services
from .serializers import NotificationSerializer

# A page, and a ceiling on one. Somebody with four hundred outstanding
# notifications does not need four hundred rows in one response; they need the
# newest fifty and a number telling them there are more.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _limit(request) -> int:
    try:
        asked = int(request.query_params.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(asked, MAX_LIMIT))


class FeedView(APIView):
    """
    What is outstanding for this caller, newest first.

    `?include_read=1` adds the ones they have already seen, for a history
    panel. Resolved notifications are never included — the feed is what is
    still true, and "we were out of sugar last Tuesday" is a report rather
    than a thing to act on.
    """

    permission_classes = [IsKnownPrincipal]

    def get(self, request):
        include_read = request.query_params.get("include_read") in {"1", "true", "yes"}
        rows = services.feed(
            request.user, limit=_limit(request), include_read=include_read
        )
        return Response(
            {
                "unread": services.unread_count(request.user),
                "results": NotificationSerializer(rows, many=True).data,
            }
        )


class UnreadView(APIView):
    """
    One integer, for the bell.

    ── WHY THIS IS POLLED AND NOT PUSHED ───────────────────────────────────
    The obvious "seamless" answer is Server-Sent Events or a WebSocket. Both
    are refused by the way this application is served, not by taste: gunicorn
    runs THREE SYNCHRONOUS WORKERS (backend/Dockerfile), and a held-open
    connection occupies one for its whole life. A shop with three tills and a
    manager's laptop would consume every worker and the API would stop
    answering — including the till that is mid-sale.

    Making push work means an async worker class and, for more than one
    process, something to fan out between them. That is a dependency and an
    operational surface, and Charter 03 §I says one enters the stack only when
    what is already there cannot do the job. A count that is at most thirty
    seconds stale does the job.

    So this endpoint is built to be cheap enough to poll: a COUNT over an
    indexed filter, no serialisation, no joins.
    """

    permission_classes = [IsKnownPrincipal]

    def get(self, request):
        return Response({"unread": services.unread_count(request.user)})


class MarkReadView(APIView):
    """
    `{"ids": [1, 2]}`, or `{}` for everything outstanding.

    POST rather than PATCH on a collection: this is not an edit to a
    notification, which nobody may edit. It is a record that somebody read it,
    and the row it writes is in a different table.
    """

    permission_classes = [IsKnownPrincipal]

    def post(self, request):
        asked = request.data.get("ids", None)

        if asked is None:
            marked = services.mark_read(request.user)
        else:
            if not isinstance(asked, list) or not all(
                isinstance(i, int) for i in asked
            ):
                return Response(
                    {"ids": "Send a list of notification ids, or omit it for all."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            marked = services.mark_read(request.user, ids=asked)

        return Response(
            {"marked": marked, "unread": services.unread_count(request.user)}
        )
