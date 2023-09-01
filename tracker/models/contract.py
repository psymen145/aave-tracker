from django.db import models


class Contract(models.Model):

    name = models.TextField(null=False, blank=False)
    network = models.ForeignKey("tracker.network", on_delete=models.PROTECT)
    # EIP-1967 Transparent Proxy: This address might be a proxy, added an implementation address
    address = models.TextField(null=False, blank=False)
    implementation_address = models.TextField(null=False, blank=False)
    abi = models.JSONField()

    created_on = models.DateTimeField(auto_now_add=True, null=True)
    last_modified = models.DateTimeField(auto_now=True, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [("network", "address")]
        ordering = ["id"]
