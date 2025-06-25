# core/signals.py

from allauth.account.signals import user_signed_up
from django.dispatch import receiver

@receiver(user_signed_up)
def populate_user_email(request, user, sociallogin=None, **kwargs):
    if sociallogin:
        email = sociallogin.account.extra_data.get('email')
        if email and not user.email:
            user.email = email
            user.save()

