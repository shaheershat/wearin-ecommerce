# core/middlewares.py

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
        request.user = None 
        request.session = SessionStore(session_key=None)
        request._is_admin_session = False

        admin_session_key = request.COOKIES.get(settings.ADMIN_SESSION_COOKIE_NAME)
        user_session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)

        logger.debug(f"Middleware: admin_session_key: {admin_session_key}, user_session_key: {user_session_key}")

        current_session_key = None

        # --- Admin session loading ---
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
                        logger.debug(f"Middleware: Admin session loaded via admin cookie. User: {user.username}.")
                    else:
                        temp_session.flush()
                        logger.warning("Middleware: Admin session invalid or not superuser.")
                else:
                    temp_session.flush()
                    logger.debug("Middleware: Invalid admin session data.")

            except Exception as e:
                logger.error(f"Middleware: Error loading admin session '{admin_session_key}': {e}", exc_info=True)

        # --- User session loading ---
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
                        logger.debug(f"Middleware: User session loaded via user cookie. User: {user.username}.")
                    else:
                        temp_session.flush()
                        logger.warning("Middleware: User session invalid or superuser.")
                else:
                    temp_session.flush()
                    logger.debug("Middleware: Invalid user session data.")

            except Exception as e:
                logger.error(f"Middleware: Error loading user session '{user_session_key}': {e}", exc_info=True)

        # --- Post-view session consistency logic ---
        if request.user and request.user.is_authenticated:
            # Ensure _auth_user_id is set
            if request.user.pk != request.session.get('_auth_user_id'):
                request.session['_auth_user_id'] = request.user.pk
                request.session.modified = True

            # Ensure _auth_user_backend is set
            if '_auth_user_backend' not in request.session:
                request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
                request.session.modified = True

            # Mark admin session if superuser
            if request.user.is_superuser:
                if request.user.pk != request.session.get('_auth_admin_id'):
                    request.session['_auth_admin_id'] = request.user.pk
                    request.session.modified = True
                request._is_admin_session = True
                logger.debug(f"Middleware: Request user is superuser {request.user.username}. Marking admin session.")
            else:
                request._is_admin_session = False
                if '_auth_admin_id' in request.session:
                    del request.session['_auth_admin_id']
                    request.session.modified = True
        else:
            request._is_admin_session = False
            for key in ['_auth_user_id', '_auth_user_backend', '_auth_admin_id']:
                if key in request.session:
                    del request.session[key]
                    request.session.modified = True

        if request.user and request.user.is_authenticated:
            logger.debug(f"Middleware: BEFORE get_response - request.user: {request.user.username}, superuser: {request.user.is_superuser}")
        else:
            logger.debug("Middleware: BEFORE get_response - Anonymous user")

        # --- Proceed to view ---
        response = self.get_response(request)

        # --- Save session if modified ---
        if request.session.modified or not request.session.session_key:
            try:
                request.session.save()
                logger.debug(f"Middleware: Session saved with key: {request.session.session_key}")
            except Exception as e:
                logger.error(f"Middleware: Failed to save session: {e}", exc_info=True)

        # --- Set correct cookie ---
        session_key_to_set = request.session.session_key
        if request._is_admin_session:
            cookie_name = settings.ADMIN_SESSION_COOKIE_NAME
        else:
            cookie_name = settings.SESSION_COOKIE_NAME

        if session_key_to_set:
            response.set_cookie(cookie_name, session_key_to_set,
                                max_age=settings.SESSION_COOKIE_AGE,
                                httponly=True,
                                secure=settings.SESSION_COOKIE_SECURE,
                                samesite=getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax'))
            logger.debug(f"Middleware: Set cookie {cookie_name}={session_key_to_set}")
        else:
            logger.warning("Middleware: No session key to set cookie.")

        return response
