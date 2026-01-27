from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    # username и password уже есть в AbstractUser
    email = models.EmailField(unique=True)

    # Убираем ненужные поля, чтобы они не болтались в базе (опционально, но чисто)
    first_name = None
    last_name = None

    def __str__(self):
        return self.username
