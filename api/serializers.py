from rest_framework import serializers
from users.models import User


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


class UserSerializer(serializers.ModelSerializer):
    """Сериализатор для модели User."""
    password = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        help_text="Пароль пользователя. Не отображается в ответах API."
    )

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password')

    def create(self, validated_data):
        """Создает нового пользователя с захешированным паролем."""
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
            password=validated_data['password']
        )
        return user

    def update(self, instance, validated_data):
        """Обновляет данные существующего пользователя."""
        password = validated_data.pop('password', None)
        if password:
            instance.set_password(password)

        return super().update(instance, validated_data)
