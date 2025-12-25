# .ENV FILE CONFIGURATION GUIDE

## 📍 Location

Save the `.env` file to:
```
C:\Users\David\Documents\N_B_A_and_N_F_L\config\.env
```

## 🔑 Your API Keys

Your existing API keys have been included in the `.env` file:

✅ **THEODDS_API_KEY** - For betting odds and lines  
✅ **SPORTSDATA_API_KEY** - For player stats and game data  
✅ **BALLDONTLIE_API_KEY** - For NBA data  
✅ **OPENAI_API_KEY** - For AI features (optional)  
✅ **WEATHER_API_KEY** - For weather conditions (NFL)  
✅ **RAPIDAPI_KEY** - For various sports APIs  

**Note**: If you have actual values for these keys, add them after the `=` sign in the .env file.

## 🚀 Quick Setup

### 1. Copy the File

```bash
# Navigate to your project
cd C:\Users\David\Documents\N_B_A_and_N_F_L

# Create config directory if it doesn't exist
mkdir config

# Copy the .env file to config folder
copy path\to\config.env config\.env
```

### 2. Generate New SECRET_KEY (Important!)

```bash
# Activate your virtual environment first
cd backend
venv\Scripts\activate

# Generate a new secret key
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copy the generated key and replace the SECRET_KEY value in your `.env` file.

### 3. Add Your API Keys

Open `config\.env` in a text editor and add your actual API key values:

```env
# Before (empty)
THEODDS_API_KEY=

# After (with your key)
THEODDS_API_KEY=your_actual_api_key_here
```

### 4. Customize Settings (Optional)

Adjust optimizer defaults if needed:

```env
DEFAULT_LINEUPS=150              # How many lineups to generate
DEFAULT_MAX_EXPOSURE=0.60        # Max 60% exposure per player
DEFAULT_MIN_UNIQUE=2             # Min 2 unique players per lineup
DEFAULT_LEVERAGE_WEIGHT=0.25     # Slight contrarian bias
```

## 📊 Configuration Sections Explained

### Django Core Settings
```env
SECRET_KEY=...          # Security key (MUST CHANGE!)
DEBUG=True              # Set False for production
ALLOWED_HOSTS=...       # Domains that can access your app
```

### Database
```env
DATABASE_URL=sqlite:///db.sqlite3    # Simple SQLite (default)
# Or PostgreSQL for production:
# DATABASE_URL=postgresql://user:pass@localhost:5432/dfs_db
```

### Project Paths
These are automatically configured based on ROOT_DIR:
```env
ROOT_DIR=C:\Users\David\Documents\N_B_A_and_N_F_L
UPLOAD_ROOT=${ROOT_DIR}\uploads
EXPORT_ROOT=${ROOT_DIR}\exports
```

### Optimizer Defaults
Control default behavior:
```env
DEFAULT_LINEUPS=150              # 1-300
DEFAULT_MAX_EXPOSURE=0.60        # 0.0-1.0 (60%)
DEFAULT_MIN_UNIQUE=2             # 1-8
DEFAULT_LEVERAGE_WEIGHT=0.25     # 0.0-2.0
```

**Leverage Weight Explained:**
- `0.0` = Ignore ownership, focus only on projections
- `0.25` = Slight contrarian bias (recommended)
- `0.5` = Moderate contrarian bias
- `1.0+` = Heavy contrarian bias (fade the chalk)

### External API Keys
Add your actual keys here:
```env
THEODDS_API_KEY=your_key_here
SPORTSDATA_API_KEY=your_key_here
BALLDONTLIE_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
WEATHER_API_KEY=your_key_here
RAPIDAPI_KEY=your_key_here
```

## 🔒 Security Best Practices

### For Development (Current Setup)
```env
DEBUG=True
SECRET_KEY=django-insecure-...  # OK for development
ALLOWED_HOSTS=localhost,127.0.0.1
```

### For Production Deployment
```env
DEBUG=False
SECRET_KEY=super-secret-random-key-here  # Generate new!
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

**⚠️ NEVER commit your .env file to Git!** (It's already in .gitignore)

## 🎯 Common Configurations

### Basic Local Development
```env
DEBUG=True
DATABASE_URL=sqlite:///db.sqlite3
DEFAULT_LINEUPS=150
DEFAULT_MAX_EXPOSURE=0.60
```

### High-Volume Processing
```env
MAX_LINEUPS_UPLOAD=10000
ENABLE_PARALLEL_OPTIMIZATION=True
OPTIMIZER_CPU_CORES=4
```

### With Redis Cache
```env
REDIS_URL=redis://localhost:6379/0
CACHE_TIMEOUT=3600
```

### With Email Notifications
```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
```

## 🧪 Testing Your Configuration

### 1. Verify Django Can Load Settings

```bash
cd C:\Users\David\Documents\N_B_A_and_N_F_L\backend
venv\Scripts\activate
python manage.py check
```

Should output: `System check identified no issues (0 silenced).`

### 2. Check Database Connection

```bash
python manage.py migrate
```

Should create database tables without errors.

### 3. Verify Static Files

```bash
python manage.py collectstatic --noinput
```

Should collect static files successfully.

### 4. Start Server

```bash
python manage.py runserver
```

Visit: http://localhost:8000

## 🔧 Troubleshooting

### Issue: "SECRET_KEY not found"
**Solution**: Make sure `.env` file is in `config/` folder and SECRET_KEY is set.

### Issue: "Cannot connect to database"
**Solution**: Check DATABASE_URL format. For SQLite, use: `sqlite:///db.sqlite3`

### Issue: "API key not working"
**Solution**: 
1. Verify API key is correct (no extra spaces)
2. Check if API has rate limits
3. Ensure API service is active

### Issue: "Paths not found"
**Solution**: Verify ROOT_DIR matches your actual project location.

## 📝 Environment Variables Priority

Django loads settings in this order:
1. Environment variables (highest priority)
2. `.env` file
3. Default values in settings.py (lowest priority)

## 🔄 Updating Configuration

### To Change Settings:
1. Edit `config\.env`
2. Restart Django server
3. No need to run migrations unless you changed database

### To Add New API Keys:
1. Add to `config\.env`:
   ```env
   NEW_API_KEY=your_key_here
   ```

2. Access in code:
   ```python
   import os
   api_key = os.getenv('NEW_API_KEY')
   ```

## 📚 Additional Resources

### API Key Sign-ups:
- **The Odds API**: https://the-odds-api.com/
- **SportsData.io**: https://sportsdata.io/
- **Ball Don't Lie**: https://www.balldontlie.io/
- **OpenAI**: https://platform.openai.com/
- **Weather API**: https://www.weatherapi.com/
- **RapidAPI**: https://rapidapi.com/

### Django Documentation:
- Settings: https://docs.djangoproject.com/en/5.0/topics/settings/
- Security: https://docs.djangoproject.com/en/5.0/topics/security/

## ✅ Configuration Checklist

- [ ] Copied config.env to config/.env
- [ ] Generated new SECRET_KEY
- [ ] Added actual API key values
- [ ] Verified ROOT_DIR path is correct
- [ ] Set DEBUG=True for development
- [ ] Customized optimizer defaults if needed
- [ ] Ran `python manage.py check`
- [ ] Successfully started server
- [ ] Tested file uploads
- [ ] Verified API keys work

## 🎉 You're Done!

Your `.env` file is now properly configured. Django will automatically load these settings when you run the server.

**Remember**: Keep your `.env` file secure and never share it publicly!

---

**Need help?** Check the main COMPLETE_UPGRADE_INSTRUCTIONS.md for more details.
