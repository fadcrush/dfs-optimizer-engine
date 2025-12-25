"""
Django settings for dfs_site project (v2.0)
"""

import sys
from pathlib import Path

"""
Django settings for dfs_site project (v2.0)
"""

import sys
from pathlib import Path
from dotenv import load_dotenv  # ← ADD THIS

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent  # C:\Users\David\Documents\N_B_A_and_N_F_L

# Load environment variables from .env file
load_dotenv(ROOT_DIR / 'config' / '.env')  # ← ADD THIS

# Add ROOT_DIR to Python path for analysis imports
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent  # C:\Users\David\Documents\N_B_A_and_N_F_L

# Add ROOT_DIR to Python path for analysis imports
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Custom paths
UPLOAD_ROOT = ROOT_DIR / "uploads"
EXPORT_ROOT = ROOT_DIR / "exports"
LOG_DIR = ROOT_DIR / "logs"

# Ensure directories exist
for directory in [UPLOAD_ROOT, EXPORT_ROOT, LOG_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Security Settings
SECRET_KEY = "django-insecure-v=#%4dk27j4ug)897*qz&_r&j8m%nv_5k!s%twtm1_-@6u16+f"
DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party
    'rest_framework',
    
    # Your DFS apps
    'apps.common',
    'apps.nba',
    'apps.nfl',
    'apps.api',
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dfs_site.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "dfs_site.wsgi.application"

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"  # DFS contests use Eastern Time
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# DFS-specific settings
DFS_SETTINGS = {
    'DEFAULT_LINEUPS': 150,
    'DEFAULT_MAX_EXPOSURE': 0.60,
    'DEFAULT_MIN_UNIQUE': 2,
    'DEFAULT_LEVERAGE_WEIGHT': 0.25,
    'MAX_LINEUPS_UPLOAD': 5000,
    'MAX_UPLOAD_SIZE_MB': 50,
}

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': LOG_DIR / 'django.log',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
        },
        'apps': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
        },
        'analysis': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
        },
    },
}