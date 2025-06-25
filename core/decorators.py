from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import get_user_model

def admin_login_required(view_func):
    """
    Decorator for views that checks if the user is logged in as an admin
    specifically through the admin login flow.
    """
    def wrapper(request, *args, **kwargs):
        # Check for the custom admin session key
        user_id = request.session.get('_auth_user_id_admin_portal')
        
        if user_id:
            try:
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                # Ensure the user is still a superuser
                if user.is_superuser:
                    request.user = user # Set request.user for the view
                    return view_func(request, *args, **kwargs)
            except User.DoesNotExist:
                # If user doesn't exist, clear the session key
                if '_auth_user_id_admin_portal' in request.session:
                    del request.session['_auth_user_id_admin_portal']
        
        # If not authenticated as admin, redirect to admin login
        messages.error(request, "Please log in as an administrator to access this page.")
        return redirect('admin_login')
    return wrapper

def user_login_required(view_func):
    """
    Decorator for views that checks if the user is logged in as a regular user
    (or a superuser acting as a regular user).
    This will essentially be a replacement for Django's built-in @login_required
    but will respect the separate session.
    """
    def wrapper(request, *args, **kwargs):
        # Check for the default Django session key
        user_id = request.session.get('_auth_user_id')

        if user_id:
            try:
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                # If the user is authenticated via the regular user flow
                # Note: A superuser can also be logged in here if they used the user login
                request.user = user # Set request.user for the view
                return view_func(request, *args, **kwargs)
            except User.DoesNotExist:
                # If user doesn't exist, clear the session key
                if '_auth_user_id' in request.session:
                    del request.session['_auth_user_id']
        
        # If not authenticated as a user, redirect to user login
        messages.error(request, "Please log in to access this page.")
        return redirect('login')
    return wrapper