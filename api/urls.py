from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import FlightSearchView, UserViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')

urlpatterns = [
    path('flights/', FlightSearchView.as_view(), name='flight-search'),
    path('', include(router.urls)),
]
