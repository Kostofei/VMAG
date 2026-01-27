from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    email = models.EmailField(blank=True, null=True, unique=False)

    first_name = None
    last_name = None

    def __str__(self):
        return self.username