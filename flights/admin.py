from django.contrib import admin
from .models import Ticket, FlightSegment


class FlightSegmentInline(admin.TabularInline):
    """Позволяет просматривать и редактировать сегменты прямо в карточке билета."""
    model = FlightSegment
    extra = 0
    # Делаем поля только для чтения, если не планируем менять данные вручную
    fields = ('order', 'airline_name', 'departure', 'departure_date', 'arrival', 'arrival_date')
    ordering = ('order',)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    """Админка для билетов с вложенными сегментами."""
    list_display = ('ticket_uid', 'get_price', 'route_type')
    list_filter = ('route_type',)
    search_fields = ('ticket_uid',)
    inlines = [FlightSegmentInline]

    @admin.display(description='Цена', ordering='price')
    def get_price(self, obj):
        return f"${obj.price}"


@admin.register(FlightSegment)
class FlightSegmentAdmin(admin.ModelAdmin):
    """Админка для отдельного просмотра сегментов (если нужно найти конкретный рейс)."""
    # Оптимизация: загружаем связанные билеты одним запросом
    list_select_related = ('ticket',)

    list_display = (
        'get_ticket_uid',
        'order',
        'airline_name',
        'get_route',
        'departure_date'
    )
    list_filter = ('airline_name', 'departure', 'arrival')
    search_fields = ('ticket__ticket_uid', 'airline_name', 'departure', 'arrival')

    @admin.display(description='Ticket UID', ordering='ticket__ticket_uid')
    def get_ticket_uid(self, obj):
        return obj.ticket.ticket_uid

    @admin.display(description='Маршрут')
    def get_route(self, obj):
        return f"{obj.departure} ➔ {obj.arrival}"