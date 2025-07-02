from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.account.adapter import DefaultAccountAdapter
from django.contrib.auth import login
from django.http import HttpResponseRedirect
from django.utils.text import slugify
import random
import string

# Helper: Generate username if missing
def generate_username(name_or_email):
    base = slugify(name_or_email.split('@')[0]) if '@' in name_or_email else slugify(name_or_email)
    suffix = ''.join(random.choices(string.digits, k=4))
    return f"{base}{suffix}"

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def save_user(self, request, sociallogin, form=None):
        user = sociallogin.user
        user.email = user.email or sociallogin.account.extra_data.get('email')

        # ✅ Set username if not set
        if not user.username:
            user.username = generate_username(user.email or "user")

        user.set_unusable_password()
        user.save()
        sociallogin.save(request)

        # ✅ Log the user in
        login(request, user)
        request.session.save()

        # ✅ Set session cookie (for user_sessionid handling)
        response = HttpResponseRedirect('/user-dashboard/')
        response.set_cookie('user_sessionid', request.session.session_key)

        raise ImmediateHttpResponse(response)

    def get_signup_redirect_url(self, request):
        return "/user-dashboard/"

class CustomAccountAdapter(DefaultAccountAdapter):
    def generate_unique_username(self, txts, regex=None):
        base_username = slugify(txts[0]) if txts else 'user'
        random_suffix = ''.join(random.choices(string.digits, k=4))
        return f"{base_username}{random_suffix}"
