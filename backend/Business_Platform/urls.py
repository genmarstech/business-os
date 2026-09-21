"""
URL configuration for Business_Platform project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include

from identity.views import LandingView

# ── LIVENESS, NOT READINESS ─────────────────────────────────────────────────
#
# Answers as long as the process is running and can route a request. It does
# NOT touch the database, deliberately: with a single instance behind Caddy,
# failing this during a brief database blip only converts a 500 into a 502 —
# nothing is gained, and a restart loop is easy to provoke.
#
# It is unauthenticated and says nothing about the system. Caddy reads it every
# ten seconds (deploy/business.caddy); anything that needs a credential cannot
# be a health check, which is the mistake this replaced — /auth/me returns 403
# to an anonymous caller, so Caddy would have marked the app permanently
# unhealthy and taken it out of rotation for good.
def healthz(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    # Somewhere for a person who types the domain to land. Without this Django
    # answers the root with a bare 404, which is what a subscriber sent here to
    # sign in was getting.
    path('', LandingView.as_view(), name='landing'),

    path('healthz', healthz, name='healthz'),
    path('admin/', admin.site.urls),
    path('org/', include('organisations.urls')),
    path('brn/', include('branches.urls')),
    path('ctl/', include('catalog.urls')),
    path('invt/', include('inventory.urls')),
    path('sls/', include('sales.urls')),
    path('auth/', include('identity.urls')),
]
