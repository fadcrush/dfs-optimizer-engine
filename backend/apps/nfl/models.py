from django.db import models
from apps.common.models import TimeStampedModel  # <-- changed

class NflSlate(TimeStampedModel):
    week = models.IntegerField()
    season = models.IntegerField()
    site = models.CharField(max_length=20)  # FD / DK

    def __str__(self):
        return f"{self.site} NFL week {self.week}, {self.season}"
