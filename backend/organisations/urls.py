from django.urls import path
from .views import greetings

# relevant paths

urlpatterns = [
    path('greet/', greetings, name='Org Greetings' )
]