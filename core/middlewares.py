from django.contrib.auth import get_user_model, load_backend
from django.contrib.sessions.backends.db import SessionStore
from django.conf import settings
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class CustomSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        logger.debug(f"Middleware: Incoming Request Path: {request.path}")

        #  Allow Django to handle normal session/auth for public/OTP pages
        safe_paths = [
            "/forgot-password/",
            "/forgot-password/verify/",
            "/forgot-password/reset/",
            "/register/",
            "/register/verify/"
        ]
        if any(request.path.startswith(p) for p in safe_paths):
            return self.get_response(request)

        # 🚀 Custom session handling for admin/user split
        request.user = None
        request._is_admin_session = False

        admin_session_key = request.COOKIES.get(settings.ADMIN_SESSION_COOKIE_NAME)
        user_session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        current_session_key = None

        logger.debug(f"Middleware: admin_session_key: {admin_session_key}, user_session_key: {user_session_key}")

        # --- Admin session ---
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
                        logger.warning("Middleware: Invalid admin session or not superuser.")
                else:
                    temp_session.flush()
                    logger.debug("Middleware: Invalid admin session data.")
            except Exception as e:
                logger.error(f"Middleware: Error loading admin session '{admin_session_key}': {e}", exc_info=True)

        # --- User session ---
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
                        logger.warning("Middleware: Invalid user session or is superuser.")
                else:
                    temp_session.flush()
                    logger.debug("Middleware: Invalid user session data.")
            except Exception as e:
                logger.error(f"Middleware: Error loading user session '{user_session_key}': {e}", exc_info=True)

        # --- Final session adjustments ---
        if request.user and request.user.is_authenticated:
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

        response = self.get_response(request)

        # --- Save and set cookie ---
        if request.session.modified or not request.session.session_key:
            try:
                request.session.save()
                logger.debug(f"Middleware: Session saved with key: {request.session.session_key}")
            except Exception as e:
                logger.error(f"Middleware: Failed to save session: {e}", exc_info=True)

        session_key_to_set = request.session.session_key
        cookie_name = settings.ADMIN_SESSION_COOKIE_NAME if request._is_admin_session else settings.SESSION_COOKIE_NAME

        if session_key_to_set:
            response.set_cookie(
                cookie_name,
                session_key_to_set,
                max_age=settings.SESSION_COOKIE_AGE,
                httponly=True,
                secure=settings.SESSION_COOKIE_SECURE,
                samesite=getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax')
            )
            logger.debug(f"Middleware: Set cookie {cookie_name}={session_key_to_set}")
        else:
            logger.warning("Middleware: No session key to set cookie.")

        return response
