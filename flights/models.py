from django.db import models


class Flight(models.Model):
    """
    Модель для хранения информации об авиарейсах, найденных в процессе парсинга.

    Используется для кэширования результатов поиска и отображения данных пользователю
    через API. Хранит информацию о маршруте, времени, авиакомпании и стоимости.
    """

    # Константы для типов маршрутов
    ONE_WAY = 'one-way'
    ROUND_TRIP = 'round-trip'
    MULTI_CITY = 'multi-city'

    ROUTE_TYPES = [
        (ONE_WAY, 'One Way'),
        (ROUND_TRIP, 'Round Trip'),
        (MULTI_CITY, 'Multi City'),
    ]

    # Основные поля
    origin = models.CharField(max_length=10, verbose_name="Аэропорт вылета")
    departure_date = models.DateTimeField(verbose_name="Дата и время вылета")

    destination = models.CharField(max_length=10, verbose_name="Аэропорт прилета")
    arrival_date = models.DateTimeField(null=True, blank=True, verbose_name="Дата и время прилета")

    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Цена")
    airline = models.CharField(max_length=100, verbose_name="Авиакомпания")
    route_type = models.CharField(
        max_length=20,
        choices=ROUTE_TYPES,
        default=ONE_WAY,
        verbose_name="Тип рейса"
    )

    # Служебные поля
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Рейс"
        verbose_name_plural = "Рейсы"
        # Логика защиты от дублей: не сохраняем один и тот же рейс
        # одной авиакомпании по той же цене на то же время
        constraints = [
            models.UniqueConstraint(
                fields=['origin', 'destination', 'departure_date', 'airline', 'price'],
                name='unique_flight_catch'
            )
        ]

    def __str__(self):
        return f"{self.origin} -> {self.destination} ({self.airline}) - {self.price}"
