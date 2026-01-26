from django.db import models


class Ticket(models.Model):
    """
    Модель для хранения билета.
    Содержит агрегированную информацию: общую стоимость и уникальный ID системы бронирования.
    """
    ROUTE_TYPES = [
        ('one_way', 'One-Way'),
        ('round_trip', 'Roundtrip'),
        ('multi_city', 'Multi-City'),
    ]

    validating_airline = models.CharField(
        max_length=100,
        verbose_name="Продавец (Валидирующая компания)",
        null=True, blank=True
    )
    ticket_uid = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        verbose_name="Идентификатор билета (из HTML)"
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Цена"
    )
    route_type = models.CharField(
        max_length=20,
        choices=ROUTE_TYPES,
        verbose_name="Тип маршрута"
    )

    class Meta:
        verbose_name = "Билет"
        verbose_name_plural = "Билеты"
        ordering = ['ticket_uid']

    def __str__(self):
        return f"TicketID {self.ticket_uid}"


class FlightSegment(models.Model):
    """
    Модель отдельного сегмента перелета.
    Хранит информацию о конкретном рейсе: аэропорты, время, авиакомпанию.
    Связана с моделью Ticket отношением многие-к-одному.
    """
    ticket = models.ForeignKey(
        Ticket,
        to_field='ticket_uid',
        db_column='ticket_uid',
        on_delete=models.CASCADE,
        related_name='segments',
        verbose_name="Билет"
    )
    operating_airline = models.CharField(
        max_length=100,
        verbose_name="Фактический перевозчик"
    )
    departure = models.CharField(
        max_length=10,
        verbose_name="Аэропорт вылета"
    )
    departure_date = models.DateTimeField(
        verbose_name="Дата и время вылета"
    )
    arrival = models.CharField(
        max_length=10,
        verbose_name="Аэропорт прилета"
    )
    arrival_date = models.DateTimeField(
        verbose_name="Дата и время прилета"
    )
    order = models.PositiveIntegerField(
        verbose_name="Порядок сегмента"
    )

    class Meta:
        verbose_name = "Сегмент перелета"
        verbose_name_plural = "Сегменты перелета"
        ordering = ['ticket', 'order']

    def __str__(self):
        return f"{self.departure} -> {self.arrival} ({self.airline_name})"
