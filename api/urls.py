from django.urls import path, include
from drf_spectacular.utils import extend_schema
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import FlightSearchView, UserViewSet, api_root

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')

decorated_token_view = extend_schema(tags=['auth'])(TokenObtainPairView)
decorated_refresh_view = extend_schema(tags=['auth'])(TokenRefreshView)

urlpatterns = [
    path('', api_root, name='api-root'),

    # Получение токена (Access + Refresh) по username и password
    path('token/', decorated_token_view.as_view(), name='token_obtain_pair'),

    # Обновление Access токена с помощью Refresh токена
    path('token/refresh/', decorated_refresh_view.as_view(), name='token_refresh'),
    path('flights/', FlightSearchView.as_view(), name='flight-search'),
    path('', include(router.urls)),
]
