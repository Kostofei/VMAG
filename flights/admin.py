from django.contrib import admin
from .models import Flight

@admin.register(Flight)
class FlightAdmin(admin.ModelAdmin):
    list_display = ('origin', 'destination', 'departure_date', 'airline', 'price', 'route_type')
    list_filter = ('route_type', 'airline', 'departure_date')
    search_fields = ('origin', 'destination', 'airline')
