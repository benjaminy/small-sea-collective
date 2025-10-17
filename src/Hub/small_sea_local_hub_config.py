# Top Matter

from pydantic_settings import BaseSettings

class SmallSeaLocalHubSettings(BaseSettings):
    app_name: str = "SmallSeaCollectiveLocalHub"
    debug: bool = False
    small_sea_root_dir_suffix: str = ""

    class Config:
        env_file = ".env"

settings = SmallSeaLocalHubSettings()
