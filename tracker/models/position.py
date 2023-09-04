from django.db import models


class Position(models.Model):
    contract = models.ForeignKey("tracker.contract", on_delete=models.PROTECT, related_name="+", null=True)
    wallet = models.ForeignKey("tracker.wallet", on_delete=models.PROTECT, related_name="+", null=True)
    network = models.ForeignKey("tracker.network", on_delete=models.PROTECT, related_name="+", null=True)
    health_factor = models.TextField()
    total_usd_collateral = models.TextField()
    last_modified = models.DateTimeField(auto_now=True, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]