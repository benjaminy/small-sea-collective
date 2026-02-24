from typing import Optional
from botocore.exceptions import ClientError

from .base import SmallSeaStorageAdapter


class SmallSeaS3Adapter(SmallSeaStorageAdapter):
    def __init__(self, s3, bucket_name):
        super().__init__(bucket_name)
        self.s3 = s3

    def download(self, path:str):
        try:
            response = self.s3.get_object(Bucket=self.zone, Key=path)
            return True, response['Body'].read(), response['ETag'].strip('"')
        except ClientError as exn:
            error_code = exn.response['Error']['Code']
            return False, None, f"Download failed: {error_code}"

    def _upload(
            self,
            path:str,
            data:bytes,
            expected_etag:Optional[str],
            content_type: str = 'application/octet-stream' ):
        try:
            if expected_etag is None:
                response = self.s3.put_object(
                    Bucket=self.zone,
                    Key=path,
                    Body=data,
                    ContentType=content_type
                )
            elif "*" == expected_etag:
                response = self.s3.put_object(
                    Bucket=self.zone,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                    IfNoneMatch=expected_etag
                )
            else:
                response = self.s3.put_object(
                    Bucket=self.zone,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                    IfMatch=expected_etag
                )
            new_etag = response['ETag'].strip('"')
            return True, new_etag, "Object updated successfully"
        except ClientError as exn:
            error_code = exn.response['Error']['Code']
            if error_code == 'PreconditionFailed':
                if expected_etag is None:
                    return False, None, "Object already exists"
                else:
                    return False, None, "ETag mismatch - object was modified"
            return False, None, f"Operation failed: {exn}"
