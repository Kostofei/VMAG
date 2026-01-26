from rest_framework import serializers


class LegSerializer(serializers.Serializer):
    """Сериализатор для одного сегмента маршрута (плеча)."""
    origin = serializers.CharField(max_length=10)
    destination = serializers.CharField(max_length=10)
    date = serializers.CharField(max_length=20)


class FlightSearchSerializer(serializers.Serializer):
    """Основной сериализатор для поиска билетов."""
    legs = LegSerializer(many=True)
    ADT = serializers.IntegerField(default=1, help_text="Взрослые")
    CNN = serializers.IntegerField(default=0, help_text="Дети")
    INF = serializers.IntegerField(default=0, help_text="Младенцы")
    cabin = serializers.ChoiceField(
        choices=[
            ('Y', 'Economy'),
            ('W', 'Premium'),
            ('C', 'Business'),
            ('F', 'First')
        ],
        default='C'
    )
