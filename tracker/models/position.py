from django.db import models
from tracker.models import Contract, Network


class Position(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="+")
    network = models.ForeignKey(Network, on_delete=models.PROTECT, related_name="+")
    health_factor = models.TextField()
    total_eth_collateral = models.TextField()
    last_modified = models.DateTimeField(auto_now=True, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]