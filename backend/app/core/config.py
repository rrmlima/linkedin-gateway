"""
Configuration settings for the application.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

# Load environment variables from .env file if it exists
# Note: With volume mount, the file should exist at /app/.env in Docker
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
print(f"[ENV] Current file: {Path(__file__)}")
print(f"[ENV] Resolved parent.parent.parent: {Path(__file__).resolve().parent.parent.parent}")
print(f"[ENV] Looking for .env at: {env_path}")
print(f"[ENV] .env file exists: {env_path.exists()}")

if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print(f"[ENV] ✓ Successfully loaded .env from: {env_path}")
    # Show a sample of what was loaded (without showing secrets)
    import os
    print(f"[ENV] Sample check - DB_HOST from env: {os.getenv('DB_HOST', 'NOT SET')}")
    print(f"[ENV] Sample check - LINKEDIN_CLIENT_ID from env: {os.getenv('LINKEDIN_CLIENT_ID', 'NOT SET')[:10]}..." if os.getenv('LINKEDIN_CLIENT_ID') else "[ENV] LINKEDIN_CLIENT_ID: NOT SET")
else:
    print(f"[ENV] ⚠ .env file not found at: {env_path}")
    print(f"[ENV] Will use environment variables from docker-compose")
    
    # List files in the directory to debug
    try:
        import os as os_module
        app_dir = Path(__file__).resolve().parent.parent.parent
        print(f"[ENV] Files in {app_dir}:")
        for item in os_module.listdir(app_dir):
            print(f"[ENV]   - {item}")
    except Exception as e:
        print(f"[ENV] Could not list directory: {e}")


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # API configuration
    API_PORT: int = Field(default=7778)
    API_HOST: str = Field(default="0.0.0.0")
    
    # Database configuration
    DB_HOST: str = Field(default="localhost")
    DB_PORT: str = Field(default="5432")
    DB_USER: str = Field(...)
    DB_PASSWORD: str = Field(...)
    DB_NAME: str = Field(default="LinkedinGateway")
    
    DB_URI: Optional[str] = None
    
    # CORS configuration
    CORS_ORIGINS: Union[str, List[str]] = Field(default="*")
    
    # JWT configuration
    JWT_SECRET_KEY: str = Field(...)
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_EXPIRATION_MINUTES: int = Field(default=60 * 24)  # 24 hours
    
    # LinkedIn OAuth
    LINKEDIN_CLIENT_ID: str = Field(...)
    LINKEDIN_CLIENT_SECRET: str = Field(...)
    LINKEDIN_REDIRECT_URI: str = Field(default="http://localhost:7778/auth/callback/linkedin")

    # Frontend URL
    FRONTEND_URL: str = Field(default="http://localhost:3000") # Assuming React default
    
    # Rate limiting
    DEFAULT_RATE_LIMIT: int = Field(default=100)
    DEFAULT_RATE_WINDOW: int = Field(default=3600)  # 1 hour in seconds
    
    @field_validator("DB_URI", mode="before")
    def assemble_db_uri(cls, v: Optional[str], info: Any) -> str:
        """
        Assemble the database URI if not provided.
        """
        if v is not None:
            return v
        
        values = info.data
        user = values.get("DB_USER")
        password = values.get("DB_PASSWORD")
        host = values.get("DB_HOST")
        port = values.get("DB_PORT")
        name = values.get("DB_NAME")
        
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
    
    @field_validator("CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        """
        Parse a comma-separated string into a list of CORS origins.
        """
        if isinstance(v, str) and v != "*":
            return v.split(",")
        return v
    

# Create settings object
settings = Settings() 