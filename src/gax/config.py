import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)
    mongo_uri: str = "mongodb://localhost:27017/?directConnection=true"
    gax_db: str = "gax"
    temporal_address: str = "localhost:7233"
    fleet_api_url: str = "http://127.0.0.1:8081"
    voyage_base_url: str = "https://ai.mongodb.com/v1"
    voyage_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-sonnet-5"
    local_broker_signing_key: SecretStr = SecretStr("")
    keycard_zone_url: str = ""


def get_settings(env_file: Path | None = ENV_FILE) -> Settings:
    if env_file:
        load_dotenv(env_file, override=False)
    values = {name: os.environ.get(name.upper()) for name in Settings.model_fields}
    return Settings(**{k: v for k, v in values.items() if v})
