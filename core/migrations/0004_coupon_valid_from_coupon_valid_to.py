# Generated by Django 4.2.23 on 2025-06-14 05:24

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_coupon_remove_product_image_alter_category_slug_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='coupon',
            name='valid_from',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='coupon',
            name='valid_to',
            field=models.DateField(blank=True, null=True),
        ),
    ]
