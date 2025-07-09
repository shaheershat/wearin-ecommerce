# settings.py

from pathlib import Path
import os
import cloudinary
import cloudinary.uploader
import cloudinary.api


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-=7w#3h#riq8x=oe2u&e)y_^)ebj4xx%8d8_18eops^bjn0*%a+'
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost'] # Moved ALLOWED_HOSTS here


# Installed apps
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'cloudinary',
    'cloudinary_storage',
    'widget_tweaks',
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'core.apps.CoreConfig',
    'crispy_forms',
    'crispy_bootstrap5', # Keep this here
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # 'core.middlewares.CustomSessionMiddleware', # <-- IMPORTANT: Commented out or remove this
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware', # Allauth middleware should be after AuthenticationMiddleware
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

SILENCED_SYSTEM_CHECKS = ['admin.E410']

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.cart_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'wearin_db',
        'USER': 'wearin_user',
        'PASSWORD': 'wearin@123',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

# Session Settings - Remove manual session cookie names to avoid conflicts
# Django's SessionMiddleware handles 'sessionid' by default
# SESSION_ENGINE = 'django.contrib.sessions.backends.db' # This is often default anyway
# SESSION_COOKIE_NAME = 'user_sessionid' # <-- REMOVED
# ADMIN_SESSION_COOKIE_NAME = 'admin_sessionid' # <-- REMOVED
SESSION_COOKIE_AGE = 86400
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_SAMESITE = 'Lax'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static and Media files
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "core/static"]
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
# SOCIALACCOUNT_ADAPTER = 'core.adapters.CustomSocialAccountAdapter'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cloudinary
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': 'dzpksiomk',
    'API_KEY': '632675563128142',
    'API_SECRET': 'UEND0Y8VjyLI9uNA9CWAC4DkcZM'
}
cloudinary.config(
    cloud_name=CLOUDINARY_STORAGE['CLOUD_NAME'],
    api_key=CLOUDINARY_STORAGE['API_KEY'],
    api_secret=CLOUDINARY_STORAGE['API_SECRET']
)

# Sites framework
SITE_ID = 1


# Authentication Backends
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Redirects
LOGIN_URL = '/login/' # Your login URL name is 'login' which maps to /login/
LOGIN_REDIRECT_URL = '/my-home/' # Redirect after successful login
LOGOUT_REDIRECT_URL = 'home' # Redirect after successful logout

# Allauth Updated Settings
ACCOUNT_LOGOUT_ON_GET = True # Can be True, but be mindful of direct logout links
ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_SIGNUP_FIELDS = ['email']

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_LOGIN_ON_GET = True # Consider setting to False for production unless specific need

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
    }
}

# Logging
import logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'core.middlewares': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'allauth': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'infoatwearin@gmail.com'
EMAIL_HOST_PASSWORD = 'xlbp ouqt keab nsod'
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
ACCOUNT_EMAIL_VERIFICATION = "none" # Important for allauth: "mandatory", "optional", "none"

# Razorpay
RAZORPAY_KEY_ID = 'rzp_test_eJqlkY9BUkrY9k'
RAZORPAY_KEY_SECRET = 'ewDtwYuzfMwBDfjNFiV8bDOk'


# ACCOUNT_ADAPTER = 'core.adapters.CustomAccountAdapter'

# CRISPY FORMS SETTINGS (ensure these are at the correct level, not inside a function)
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Remove any view functions or decorators from here! They belong in views.py or decorators.py
# @user_login_required # This was incorrect here
# def user_dashboard_view(request): # This was incorrect here
#     return render(request, 'user/main/authenticated_home.html') # This was incorrect here