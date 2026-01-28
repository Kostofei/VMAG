from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from .models import User

# Импортируем тот же ключ, который использовали во вьюсете
USER_CACHE_KEY = "users_list_cache"


@receiver([post_save, post_delete], sender=User)
def clear_user_cache(sender, instance, **kwargs):
    """
    Сигнал для автоматической очистки кеша пользователей.

    Срабатывает при любом сохранении (создание/обновление)
    или удалении пользователя.
    """
    cache.delete(USER_CACHE_KEY)