from pathlib import Path
import os
import cloudinary
import cloudinary.uploader
import cloudinary.api

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-=7w#3h#riq8x=oe2u&e)y_^)ebj4xx%8d8_18eops^bjn0*%a+'
DEBUG = True
ALLOWED_HOSTS = []

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
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',   # ✅ Keep this
    'core.middlewares.CustomSessionMiddleware',               # ✅ After SessionMiddleware
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
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
                'django.template.context_processors.request',  # Required by allauth
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
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

# Sessions (ensure these are present and correct)
SESSION_ENGINE = 'django.contrib.sessions.backends.db' # Ensure this is present
SESSION_COOKIE_NAME = 'user_sessionid' # Default for regular users
ADMIN_SESSION_COOKIE_NAME = 'admin_sessionid'

# Password validators
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

# Static & Media files
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "core/static"]
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cloudinary Config
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

# Site & Auth
SITE_ID = 2
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = 'user_dashboard'
LOGOUT_REDIRECT_URL = 'home'

# Allauth Settings (Clean & Final)
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_SIGNUP_FIELDS = ['email']

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_ADAPTER = 'core.adapters.NoPromptSocialAccountAdapter'

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
    }
}

# Sessions
SESSION_COOKIE_AGE = 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_SAMESITE = 'Lax'

# Logging for debugging (REQUIRED FOR MIDDLEWARE DEBUGGING)
import logging # Already present, but confirming
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
        'level': 'INFO', # Default level for other loggers
    },
    'loggers': {
        'core.middlewares': { # Target your custom middleware's logger for detailed output
            'handlers': ['console'],
            'level': 'DEBUG', # VERY IMPORTANT: Set to DEBUG to see all custom middleware logs
            'propagate': False,
        },
        'allauth': { # Keep your existing allauth logging setup
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'django': { # Example for general Django logging
            'handlers': ['console'],
            'level': 'INFO', # Keep this at INFO or WARNING for less verbosity
            'propagate': False,
        },
    },
}


EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True

EMAIL_HOST_USER = 'infoatwearin@gmail.com'
EMAIL_HOST_PASSWORD = 'xlbp ouqt keab nsod'  # paste the 16-character app password
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER