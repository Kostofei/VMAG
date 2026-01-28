from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache

from api.views import USER_CACHE_KEY
from .models import User


@receiver([post_save, post_delete], sender=User)
def clear_user_cache(sender, instance, **kwargs):
    """
    Сигнал для автоматической очистки кеша пользователей.

    Срабатывает при любом сохранении (создание/обновление)
    или удалении пользователя.
    """
    cache.delete(USER_CACHE_KEY)