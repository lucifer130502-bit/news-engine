import os
import yaml
from pathlib import Path
from .models.config import Config

# Environment variable → YAML path mapping for sensitive fields.
# Each tuple is (ENV_VAR_NAME, yaml_section, yaml_key).
_ENV_OVERRIDES = [
    # API keys
    ("SLACK_WEBHOOK_URL",     "api_keys", "slack_webhook_url"),
    # LLM Gateway
    ("LLM_GATEWAY_BASE_URL",  "llm_gateway", "base_url"),
    ("LLM_GATEWAY_API_KEY",   "llm_gateway", "api_key"),
    # App
    ("LLM_MODEL",             "app",      "llm_model"),
    # MinIO
    ("MINIO_ENDPOINT",        "minio",    "endpoint"),
    ("MINIO_ACCESS_KEY",      "minio",    "access_key"),
    ("MINIO_SECRET_KEY",      "minio",    "secret_key"),
    # MySQL
    ("MYSQL_HOST",            "mysql",    "host"),
    ("MYSQL_PORT",            "mysql",    "port"),
    ("MYSQL_USER",            "mysql",    "user"),
    ("MYSQL_PASSWORD",        "mysql",    "password"),
    ("MYSQL_DATABASE",        "mysql",    "database"),
    # SEBI
    ("SEBI_SLACK_WEBHOOK",    "sebi",     ("slack", "webhook_url")),
    ("SEBI_SMTP_HOST",        "sebi",     ("email", "smtp_host")),
    ("SEBI_SMTP_PORT",        "sebi",     ("email", "smtp_port")),
    ("SEBI_SMTP_USER",        "sebi",     ("email", "smtp_user")),
    ("SEBI_SMTP_PASS",        "sebi",     ("email", "smtp_pass")),
    ("SEBI_FROM_ADDR",        "sebi",     ("email", "from_addr")),
]


def _apply_env_overrides(config_data: dict) -> dict:
    """Override YAML values with environment variables when set.

    `key` may be a string (top-level of the section) or a tuple path for
    nested sections (e.g. ("slack", "webhook_url") under `sebi`).
    """
    for env_var, section, key in _ENV_OVERRIDES:
        value = os.getenv(env_var)
        if value is None:
            continue
        if section not in config_data or config_data[section] is None:
            config_data[section] = {}

        # Coerce types for numeric ports
        final_key = key[-1] if isinstance(key, tuple) else key
        if final_key in ("port", "smtp_port"):
            try:
                value = int(value)
            except ValueError:
                pass

        if isinstance(key, tuple):
            node = config_data[section]
            for part in key[:-1]:
                if part not in node or node[part] is None:
                    node[part] = {}
                node = node[part]
            node[key[-1]] = value
        else:
            config_data[section][key] = value
    return config_data


def load_config(env: str = "dev") -> Config:
    if not env:
        env = "dev"

    config_file = Path(__file__).parent / f"{env}.yaml"
    if not config_file.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_file}. "
            f"Available profiles: dev, beta, prod"
        )

    with open(config_file, "r") as f:
        config_data = yaml.safe_load(f)

    config_data = _apply_env_overrides(config_data)
    return Config(**config_data)
