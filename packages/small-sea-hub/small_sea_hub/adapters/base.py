from typing import Optional


class SmallSeaStorageAdapter:
    def __init__(
            self,
            zone:str):
        self.zone = zone

    def upload_overwrite(
            self,
            path:str,
            data:bytes,
            content_type: str = 'application/octet-stream'):
        return self._upload(path, data, None, content_type)

    def upload_fresh(
            self,
            path:str,
            data:bytes,
            content_type: str = 'application/octet-stream'):
        return self._upload(path, data, "*", content_type)

    def upload_if_match(
            self,
            path:str,
            data:bytes,
            expected_etag:str,
            content_type: str = 'application/octet-stream'):
        return self._upload(path, data, expected_etag, content_type)
