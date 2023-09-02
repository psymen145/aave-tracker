from django.db import models


class Wallet(models.Model):

    name = models.TextField(null=False, blank=False)
    network = models.ForeignKey("tracker.network", on_delete=models.PROTECT, null=True)
    address = models.TextField(null=False, blank=False)
    created_on = models.DateTimeField(auto_now_add=True, null=True)
    last_modified = models.DateTimeField(auto_now=True, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [("network", "address")]
        ordering = ["id"]
