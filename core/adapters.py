from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

class NoPromptSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        # Always allow auto-signup without additional forms
        return True
