from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.helpers import render_authentication_error

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def authentication_error(self, request, provider_id=None, error=None, exception=None, extra_context=None):
        context = {
            "auth_error": {
                "provider": provider_id,
                "code": error,
                "exception": exception,
            }
        }
        if extra_context:
            context.update(extra_context)

        # ✅ FIX: Pass context as the second positional argument, not as a keyword
        return render_authentication_error(request, context)

    def on_authentication_error(self, request, provider_id, error=None, exception=None, extra_context=None):
        # Forward to old method to keep it centralized
        return self.authentication_error(request, provider_id, error, exception, extra_context)
