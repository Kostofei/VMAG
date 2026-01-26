from django.urls import path
from .views import FlightSearchView

urlpatterns = [
    path('flights/', FlightSearchView.as_view(), name='flight-search'),
]