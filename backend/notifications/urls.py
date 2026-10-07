"""Notification routes. Mounted at /ntf/ by Business_Platform/urls.py."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.FeedView.as_view(), name="notification-feed"),
    # Its own endpoint, and the only one a till polls. The feed is a page of
    # rows; this is one integer, so the poll that runs every half minute on
    # every open terminal costs a count rather than a serialisation.
    path("unread", views.UnreadView.as_view(), name="notification-unread"),
    path("read", views.MarkReadView.as_view(), name="notification-read"),
]
