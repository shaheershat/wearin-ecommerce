from allauth.account.signals import user_signed_up
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.contrib.auth.models import User
from .models import Wallet

@receiver(user_signed_up)
def populate_user_email(request, user, sociallogin=None, **kwargs):
    if sociallogin:
        email = sociallogin.account.extra_data.get('email')
        if email and not user.email:
            user.email = email
            user.save()

@receiver(post_save, sender=User)
def create_user_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(user=instance)