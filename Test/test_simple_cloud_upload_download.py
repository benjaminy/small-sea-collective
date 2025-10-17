import os
import subprocess
import tempfile
import shutil
import pytest
import pathlib
import time
import boto3
import uuid

def upload_object( s3, bucket_name, key, data, expected_tag=None ):
    """
    """

    try:
        if expected_tag is None:
            put_response = s3.put_object(Bucket=bucket_name, Key=key, Body=data)
            return {"success": True, "response": put_response}

        overwrite_response = s3.put_object(
            Bucket= bucket_name,
            Key= key,
            Body= data,
            IfMatch= expected_tag
        )

        return { "success": True, "response": overwrite_response }

    except Exception as exn:
        return { "success": False, "exn": str( exn ) }

def test_simple_up_down(minio_server_gen):
    minio_server = minio_server_gen()
    s3 = boto3.client(
        "s3",
        endpoint_url=minio_server["endpoint"],
        aws_access_key_id=minio_server["access_key"],
        aws_secret_access_key=minio_server["secret_key"]
    )
    my_bucket = "alice"
    s3.create_bucket(Bucket=my_bucket)
    # test_file = local_dir / "blah.txt"
    # with open( test_file, "w" ) as f:
    #     f.write( "HELLO WORLD" )
    data = b"The quick brown fox"
    key = "Fred"
    upload_result = upload_object(s3, my_bucket, key, data, expected_tag=None)
    assert(upload_result["success"])
    etag = upload_result["response"]["ETag"]
    print( f"UPLOAD RESULT  {upload_result} {etag}" )
    data = b"The quick brown fox 2"
    overwrite_result = upload_object(s3, my_bucket, key, data, expected_tag=etag)
    if overwrite_result["success"]:
        print( "YAY" )
    else:
        print( f"SAD FACE {overwrite_result['exn']}" )
        assert( False )
    etag = overwrite_result["response"]["ETag"]
    # test2_file = local_dir / "snerp.txt"
    # with open( test2_file, "r") as f:
    #     hello = f.read()
    #     assert("HELLO WORLD" == hello)
    # raise Exception()


# def test_minio_fixture(minio_server):
#     s3 = minio_server["s3"]
#     bucket = minio_server["bucket"]

#     # Put an object
#     s3.put_object(Bucket=bucket, Key="foo.txt", Body=b"hello")

#     # Get it back
#     obj = s3.get_object(Bucket=bucket, Key="foo.txt")
#     assert obj["Body"].read() == b"hello"
    

if __name__ == "__main__":
    tester( temp_env )
