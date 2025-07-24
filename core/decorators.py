from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse 
from functools import wraps

def admin_login_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        print(f"DECORATOR DEBUG: admin_login_required called for path: {request.path}")
        print(f"DECORATOR DEBUG: request.user object: {request.user}")
        print(f"DECORATOR DEBUG: request.user.is_authenticated: {request.user.is_authenticated}")
        print(f"DECORATOR DEBUG: request.user.is_superuser: {request.user.is_superuser}")

        if request.user.is_authenticated and request.user.is_superuser:
            print(f"DECORATOR DEBUG: User {request.user.username} is authenticated AND superuser. Proceeding to view.")
            return view_func(request, *args, **kwargs)
        else:
            print(f"DECORATOR DEBUG: User is NOT authenticated OR NOT superuser. Redirecting to admin login.")
            messages.error(request, "Please log in as an administrator to access this page.")
            return redirect(reverse('admin_login'))
    return _wrapped_view


def user_login_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_superuser:
            if not request.user.is_active:
                messages.error(request, "Your account is inactive. Please contact support.")
                return redirect(reverse('login'))
            return view_func(request, *args, **kwargs)
        else:
            messages.error(request, "Please log in to access this page.")
            return redirect(f"{reverse('login')}?next={request.path}")
    return _wrapped_view