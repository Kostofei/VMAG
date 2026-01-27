import hashlib
import json
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from asgiref.sync import async_to_sync

from flights.services import FlightParser
from .serializers import FlightSearchSerializer


class FlightSearchView(APIView):
    """
    Эндпоинт для поиска авиабилетов.
    Сначала проверяет кэш Redis, если данных нет — запускает парсинг.
    """

    def post(self, request):
        serializer = FlightSearchSerializer(data=request.data)

        # Создаем уникальный ключ для замка.
        lock_id = f"lock_{request.user.id}"

        # Пытаемся установить замок на 60 секунд (время жизни ключа)
        is_locked = not cache.add(lock_id, "true", timeout=60)

        if is_locked:
            return Response(
                {"detail": "Парсинг уже запущен. Пожалуйста, подождите завершения."},
                status=status.HTTP_409_CONFLICT
            )

        if serializer.is_valid():
            search_data = serializer.validated_data

            print(search_data)

            # Генерируем уникальный ключ для кэша на основе параметров поиска
            cache_key = self._generate_cache_key(search_data)

            # Пытаемся взять данные из Redis
            cached_data = cache.get(cache_key)
            if cached_data:
                # Снимаем замок
                cache.delete(lock_id)
                return Response(cached_data, status=status.HTTP_200_OK)

            # Если в кэше пусто — здесь будет вызов нашего Playwright сервиса
            parser = FlightParser()
            # Превращаем асинхронный запуск в обычный вызов
            found_flights = async_to_sync(parser.run)(search_data)

            # Сохраняем результат в кэш на 30 минут
            cache.set(cache_key, found_flights, timeout=60 * 30)

            # Снимаем замок
            cache.delete(lock_id)

            return Response(found_flights, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _generate_cache_key(data):
        """Создает MD5 хэш от параметров запроса для использования в качестве ключа кэша."""
        encoded_data = json.dumps(data, sort_keys=True).encode()
        return f"flights_search_{hashlib.md5(encoded_data).hexdigest()}"
