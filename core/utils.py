import random
from django.core.mail import send_mail
from .models import EmailOTP
from django.conf import settings


def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp):
    subject = 'Your Wearin OTP Code'
    message = f'Hi,\n\nYour OTP for verification is: {otp}\n\nIf you didn’t request this, please ignore this email.\n\n– Wearin Team'
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email])

