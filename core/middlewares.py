from django.contrib.auth import get_user_model, load_backend
from django.contrib.sessions.backends.db import SessionStore
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
import logging

logger = logging.getLogger(__name__)
User = get_user_model()

class CustomSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        logger.debug(f"Middleware: Incoming Request Path: {request.path}")

        bypass_paths = [
            "/admin/login/", "/admin/logout/",
            "/forgot-password/", "/forgot-password/verify/",
            "/forgot-password/reset/", "/register/", "/register/verify/",
            "/accounts/google/login/", "/accounts/google/login/callback/",
            "/accounts/3rdparty/signup/", "/accounts/login/"
        ]
        if any(request.path.startswith(p) for p in bypass_paths):
            return self.get_response(request)

        try:
            if request.session.get('_auth_user_id'):
                logger.debug("Middleware: Django session already valid. Skipping manual session handling.")
                return self.get_response(request)
        except Exception as e:
            logger.error(f"Middleware: Error checking session: {e}", exc_info=True)

        request.user = AnonymousUser()
        request._is_admin_session = False

        admin_session_key = request.COOKIES.get(settings.ADMIN_SESSION_COOKIE_NAME)
        user_session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        current_session_key = None

        logger.debug(f"Middleware: admin_session_key: {admin_session_key}, user_session_key: {user_session_key}")

        # --- Admin session handling ---
        if admin_session_key:
            try:
                temp_session = SessionStore(session_key=admin_session_key)
                temp_session.load()

                admin_user_id = temp_session.get('_auth_admin_id')
                auth_backend_path = temp_session.get('_auth_user_backend')

                if admin_user_id and auth_backend_path:
                    backend = load_backend(auth_backend_path)
                    user = backend.get_user(admin_user_id)

                    if user and user.is_superuser:
                        request.session = temp_session
                        request.user = user
                        request._is_admin_session = True
                        current_session_key = admin_session_key
                        logger.debug(f"Middleware: Admin session loaded. User: {user.username}")
                else:
                    temp_session.flush()
            except Exception as e:
                logger.error(f"Middleware: Error loading admin session: {e}", exc_info=True)

        # --- User session handling ---
        if not current_session_key and user_session_key:
            try:
                temp_session = SessionStore(session_key=user_session_key)
                temp_session.load()

                user_id = temp_session.get('_auth_user_id')
                auth_backend_path = temp_session.get('_auth_user_backend')

                if user_id and auth_backend_path:
                    backend = load_backend(auth_backend_path)
                    user = backend.get_user(user_id)

                    if user and not user.is_superuser:
                        request.session = temp_session
                        request.user = user
                        current_session_key = user_session_key
                        logger.debug(f"Middleware: User session loaded. User: {user.username}")
                else:
                    temp_session.flush()
            except Exception as e:
                logger.error(f"Middleware: Error loading user session: {e}", exc_info=True)

        # --- Final session sync ---
        if hasattr(request, 'user') and request.user.is_authenticated:
            if request.user.pk != request.session.get('_auth_user_id'):
                request.session['_auth_user_id'] = request.user.pk
                request.session.modified = True

            if '_auth_user_backend' not in request.session:
                request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
                request.session.modified = True

            if request.user.is_superuser:
                if request.user.pk != request.session.get('_auth_admin_id'):
                    request.session['_auth_admin_id'] = request.user.pk
                    request.session.modified = True
                request._is_admin_session = True
            else:
                if '_auth_admin_id' in request.session:
                    del request.session['_auth_admin_id']
                    request.session.modified = True
                request._is_admin_session = False
        else:
            request._is_admin_session = False
            for key in ['_auth_user_id', '_auth_user_backend', '_auth_admin_id']:
                if key in request.session:
                    del request.session[key]
                    request.session.modified = True

        logger.debug(f"Middleware: BEFORE get_response - user: {request.user}, is_admin: {request._is_admin_session}")

        # --- Process the response ---
        response = self.get_response(request)

        # --- Save session if modified ---
        try:
            if request.session.modified or not request.session.session_key:
                request.session.save()
                logger.debug(f"Middleware: Session saved with key: {request.session.session_key}")
        except Exception as e:
            logger.error(f"Middleware: Failed to save session: {e}", exc_info=True)

        # --- Set appropriate session cookie ---
        session_key_to_set = request.session.session_key
        cookie_name = settings.ADMIN_SESSION_COOKIE_NAME if getattr(request, '_is_admin_session', False) else settings.SESSION_COOKIE_NAME

        if session_key_to_set:
            response.set_cookie(
                cookie_name,
                session_key_to_set,
                max_age=settings.SESSION_COOKIE_AGE,
                httponly=True,
                secure=getattr(settings, 'SESSION_COOKIE_SECURE', False),
                samesite=getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax')
            )
            logger.debug(f"Middleware: Set cookie {cookie_name}={session_key_to_set}")
        else:
            logger.warning("Middleware: No session key to set cookie.")

        return response
