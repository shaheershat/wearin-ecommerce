# Generated by Django 4.2.23 on 2025-07-02 08:07

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_merge_20250702_1332'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='order',
            name='order_number',
        ),
        migrations.RemoveField(
            model_name='orderitem',
            name='is_cancelled',
        ),
        migrations.RemoveField(
            model_name='orderitem',
            name='is_returned',
        ),
        migrations.RemoveField(
            model_name='orderitem',
            name='return_requested',
        ),
        migrations.DeleteModel(
            name='ReturnRequest',
        ),
    ]
