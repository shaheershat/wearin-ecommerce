# Generated by Django 4.2.23 on 2025-06-26 07:26

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_emailotp_otp'),
    ]

    operations = [
        migrations.RenameField(
            model_name='emailotp',
            old_name='code',
            new_name='otp',
        ),
        migrations.RemoveField(
            model_name='emailotp',
            name='is_used',
        ),
        migrations.AlterField(
            model_name='emailotp',
            name='purpose',
            field=models.CharField(max_length=20),
        ),
    ]
