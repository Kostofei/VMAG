from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Путь для скачивания самой схемы (YAML/JSON)
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),

    # Swagger UI:
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

    # Redoc (альтернативный интерфейс):
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    path('api-auth/', include('rest_framework.urls')),
    path('api-v1/', include('api.urls')),

    path('admin/', admin.site.urls),
    path("__debug__/", include("debug_toolbar.urls")),
]
