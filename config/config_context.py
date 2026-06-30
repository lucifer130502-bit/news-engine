import os
from .config_loader import load_config

# Load config once — globally available
app_profile = os.getenv("APP_PROFILE", "dev")
config = load_config(app_profile)
