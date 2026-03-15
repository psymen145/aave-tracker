from django.db import migrations


CHAINS = [
    {
        "name": "Arbitrum One",
        "pool_address": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    },
    {
        "name": "Optimism",
        "pool_address": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    },
    {
        "name": "Base",
        "pool_address": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    },
]


def add_chains(apps, schema_editor):
    Network = apps.get_model("tracker", "Network")
    Contract = apps.get_model("tracker", "Contract")

    # Reuse the mainnet Pool ABI — all Aave V3 deployments share the same interface
    mainnet_pool = Contract.objects.get(name="Pool", network_id=2)
    pool_abi = mainnet_pool.abi

    for chain in CHAINS:
        network, _ = Network.objects.get_or_create(name=chain["name"])
        Contract.objects.get_or_create(
            name="Pool",
            network=network,
            defaults={
                "address": chain["pool_address"],
                "abi": pool_abi,
            },
        )


def remove_chains(apps, schema_editor):
    Network = apps.get_model("tracker", "Network")
    Network.objects.filter(name__in=[c["name"] for c in CHAINS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0008_rename_total_eth_collateral_position_total_usd_collateral"),
    ]

    operations = [
        migrations.RunPython(add_chains, remove_chains, elidable=False),
    ]
