# Generated by Django 4.0.7 on 2023-09-02 22:02

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0006_alter_position_contract_alter_position_network_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='wallet',
            name='name',
            field=models.TextField(blank=True, null=True),
        ),
    ]