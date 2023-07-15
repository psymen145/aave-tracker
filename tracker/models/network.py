from enum import Enum

from django.db import models


class Network(models.Model):

    class NetworkName(Enum):
        ETH_GOERLI = 1

    name = models.TextField()
    created_on = models.DateTimeField(auto_now_add=True, null=True)
    last_modified = models.DateTimeField(auto_now=True, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]