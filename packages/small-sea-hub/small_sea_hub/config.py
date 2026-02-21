# Top Matter

import platformdirs
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SMALL_SEA_",
        toml_file="small_sea_hub.toml",
    )

    root_dir: str = ""
    port: int = 11437
    app_name: str = "SmallSeaCollectiveCore"
    app_author: str = "Benjamin Ylvisaker"
    debug: bool = False

    def get_root_dir(self) -> str:
        if self.root_dir:
            return self.root_dir
        return platformdirs.user_data_dir(self.app_name, self.app_author)
