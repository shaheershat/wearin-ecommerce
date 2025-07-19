# settings.py
print("Loading settings.py...") 
from pathlib import Path
import os
import cloudinary
import cloudinary.uploader
import cloudinary.api
from datetime import timedelta # Import timedelta for Celery Beat schedule

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-=7w#3h#riq8x=oe2u&e)y_^)ebj4xx%8d8_18eops^bjn0*%a+'

# SECURITY WARNING: don't run with debug true in production!
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']


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
    'core.apps.CoreConfig', # Your core app
    'crispy_forms',
    'crispy_tailwind',
    'django_celery_beat',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware', # Allauth middleware should be after AuthenticationMiddleware
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# This check relates to admin site and is generally fine to silence if you understand it.
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
                'core.context_processors.offer_context',
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

# Session Settings
SESSION_COOKIE_AGE = 86400 # 24 hours
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
TIME_ZONE = 'Asia/Kolkata' # Ensure this matches your Celery TIME_ZONE
USE_I18N = True
USE_TZ = True # Use timezone-aware datetimes

# Static and Media files
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "core/static"]
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cloudinary Configuration
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': 'dzpksiomk',
    'API_KEY': '632675563128142',
    'API_SECRET': 'UEND0Y8VjyLI9uNA9CWAC4DkcZM' # SECURITY WARNING: Use environment variables for production!
}
cloudinary.config(
    cloud_name=CLOUDINARY_STORAGE['CLOUD_NAME'],
    api_key=CLOUDINARY_STORAGE['API_KEY'],
    api_secret=CLOUDINARY_STORAGE['API_SECRET']
)

# Sites framework (required by allauth)
SITE_ID = 1


# Authentication Backends (required by allauth)
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Redirects
LOGIN_URL = '/login/' # Your login URL name is 'login' which maps to /login/
LOGIN_REDIRECT_URL = '/my-home/' # Redirect after successful login
LOGOUT_REDIRECT_URL = 'home' # Redirect after successful logout (should be a URL name or path)

# Django-allauth Updated Settings
ACCOUNT_LOGOUT_ON_GET = True # Consider changing to False for production for CSRF protection
ACCOUNT_LOGIN_METHODS = ['email'] # Replaces ACCOUNT_AUTHENTICATION_METHOD
ACCOUNT_SIGNUP_FIELDS = ['email'] # Consistent signup fields
ACCOUNT_USERNAME_REQUIRED = False # NEW: Explicitly set to False for email-only flow

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_LOGIN_ON_GET = True # Consider changing to False for production for CSRF protection

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
    }
}

# Logging Configuration
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
            'level': 'DEBUG', # Set to DEBUG to see all log levels in console
            'class': 'logging.StreamHandler',
            'formatter': 'verbose', # Changed to verbose to see module and process info
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG', # Set root logger to DEBUG to catch everything by default
    },
    'loggers': {
        'core': { # Target your 'core' app's logs
            'handlers': ['console'],
            'level': 'DEBUG', # Set to DEBUG for detailed logs from your app
            'propagate': False,
        },
        'core.middlewares': { # If you have a specific middleware logger
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
            'level': 'INFO', # Keep Django's own logs at INFO or higher if too verbose
            'propagate': False,
        },
        'django.db.backends': { # Good for seeing database queries in DEBUG
            'handlers': ['console'],
            'level': 'INFO', # Keep at INFO unless you need to debug SQL queries
            'propagate': False,
        },
    },
}
CELERY_IMPORTS = (
    'core.tasks',   # Assuming you have a core/tasks.py for other tasks
    'core.emails',  # <--- ADDED THIS LINE to tell Celery about tasks in core/emails.py
)

# Email Settings
# For development, print emails to console. For production, use SMTP.
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# Define these variables even if not directly used by console.EmailBackend
# They are needed if DEFAULT_FROM_EMAIL references EMAIL_HOST_USER,
# or for when you switch to SMTP in production.
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'infoatwearin@gmail.com' # This variable is now explicitly defined
EMAIL_HOST_PASSWORD = 'xlbp ouqt keab nsod' # SECURITY WARNING: Use environment variables for production!

DEFAULT_FROM_EMAIL = EMAIL_HOST_USER # This line will now work without NameError

ACCOUNT_EMAIL_VERIFICATION = "none" # "mandatory", "optional", "none" - set as per your needs

# Razorpay API Keys (SECURITY WARNING: Use environment variables for production!)
RAZORPAY_KEY_ID = 'rzp_test_eJqlkY9BUkrY9k'
RAZORPAY_KEY_SECRET = 'ewDtwYuzfMwBDfjNFiV8bDOk'

# CRISPY FORMS SETTINGS
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Celery Configuration
CELERY_BROKER_URL = 'redis://localhost:6379/0'  # Make sure Redis is running
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Kolkata'  # Match your Django TIME_ZONE
CELERY_ENABLE_UTC = False  # Set to False if using local time zone
CELERY_TASK_ALWAYS_EAGER = False

# Celery Beat Schedule - For periodic tasks like expiring reservations
CELERY_BEAT_SCHEDULE = {
    'run-expire-reservations': {
        'task': 'django.core.management.call_command',
        'args': ['expire_reservations'],
        'schedule': timedelta(minutes=1),
        'options': {'queue': 'default'},
    },
    'send-scheduled-newsletters': {
        'task': 'core.tasks.send_scheduled_campaigns',
        'schedule': timedelta(minutes=1),
        'options': {'queue': 'default'},
    },
}

SESSION_SAVE_EVERY_REQUEST = True
# For email absolute URLs (necessary for emails sent by Celery tasks or when generating links)
SITE_URL = 'http://127.0.0.1:8000' # SECURITY WARNING: Change to your actual domain in production!

# --- ADDED THIS SECTION ---
SITE_NAME = "WEARIN" # Define your site name here
# --- END ADDED SECTION ---