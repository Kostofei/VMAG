import hashlib
import json
from django.core.cache import cache
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from asgiref.sync import async_to_sync
from drf_spectacular.utils import extend_schema, OpenApiExample
from rest_framework.decorators import api_view
from rest_framework.reverse import reverse

from .serializers import FlightSearchSerializer, UserSerializer
from flights.services import FlightParser
from users.models import User

USER_CACHE_KEY = "users_list_cache"


@extend_schema(tags=['flights'])
class FlightSearchView(APIView):
    """
    Эндпоинт для поиска авиабилетов.
    Сначала проверяет кэш Redis, если данных нет — запускает парсинг.
    """

    @extend_schema(
        request=FlightSearchSerializer,
        responses={200: FlightSearchSerializer(many=True)},
        description="Эндпоинт для поиска авиабилетов..."
    )
    def post(self, request):
        serializer = FlightSearchSerializer(data=request.data)

        # Создаем уникальный ключ для замка.
        if not request.user.is_authenticated:
            if not request.session.session_key:
                request.session.create()

            session_key = request.session.session_key
            lock_id = f"lock_guest_{session_key}"
        else:
            lock_id = f"lock_{request.user.id}"

        # Пытаемся установить замок на 60 секунд
        is_locked = not cache.add(lock_id, "true", timeout=60)

        if is_locked:
            return Response(
                {"detail": "Парсинг уже запущен. Пожалуйста, подождите завершения."},
                status=status.HTTP_409_CONFLICT
            )

        if serializer.is_valid():
            search_data = serializer.validated_data

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
            if isinstance(found_flights, list) or found_flights == "No flights found matching your search":
                cache.set(cache_key, found_flights, timeout=60 * 30)
                status_code = status.HTTP_200_OK
            else:
                status_code = status.HTTP_400_BAD_REQUEST
                found_flights = {"detail": found_flights}

            # Снимаем замок
            cache.delete(lock_id)

            return Response(found_flights, status=status_code)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _generate_cache_key(data):
        """Создает MD5 хэш от параметров запроса для использования в качестве ключа кэша."""
        encoded_data = json.dumps(data, sort_keys=True).encode()
        return f"flights_search_{hashlib.md5(encoded_data).hexdigest()}"


@extend_schema(tags=['users'])
class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления пользователями.

    Обеспечивает стандартные CRUD-операции:
    - GET: Список пользователей (list) или отдельный пользователь (retrieve).
    - POST: Регистрация нового пользователя (create).
    - PUT/PATCH: Обновление данных пользователя (update).
    - DELETE: Удаление пользователя (destroy).
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=UserSerializer,
        examples=[
            OpenApiExample(
                'Пример регистрации',
                summary='Создание нового пользователя',
                description='Пример запроса для регистрации пользователя',
                value={
                    'username': 'пользователь',
                    'email': 'user@example.com',
                    'password': 'пароль'
                },
                request_only=True,
                response_only=False,
            ),
        ]
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        """
        Возвращает список пользователей. Данные кешируются в Redis.

        Сначала проверяется наличие данных в кеше. Если данных нет,
        делается запрос к БД, и результат сохраняется в Redis.
        """
        cached_data = cache.get(USER_CACHE_KEY)

        if cached_data:
            return Response(cached_data)

        # Если в кеше пусто — получаем данные через стандартный метод
        response = super().list(request, *args, **kwargs)

        # Сохраняем данные в кеш
        cache.set(USER_CACHE_KEY, response.data, timeout=None)

        return response

    def get_queryset(self):
        """Возвращает набор данных для текущего запроса."""
        return super().get_queryset()

    def get_permissions(self):
        """
        Назначает права доступа в зависимости от выполняемого действия.
        - Регистрация (create): Разрешена всем.
        - Остальные действия: Требуют авторизации.
        """
        if self.action == 'create':
            return [AllowAny()]
        return [IsAuthenticated()]


@api_view(['GET'])
def api_root(request, format=None):
    """Корневой эндпоинт API, возвращающий список всех доступных ресурсов."""
    return Response({
        'users': reverse('user-list', request=request),
        'flights': reverse('flight-search', request=request),
        'token_obtain_pair': reverse('token_obtain_pair', request=request),
        'token_refresh': reverse('token_refresh', request=request),
    })
