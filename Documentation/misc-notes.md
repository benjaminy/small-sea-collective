
uvicorn --app-dir Source small_sea_local_hub:app --reload --port 11437
uvicorn --app-dir Source small_sea_local_hub:app --reload --port ${SMALL_SEA_HUB_PORT}

uv run fastapi dev Source/small_sea_local_hub.py

`rclone serve webdav --addr :PORT LOCAL_PATH --user USER --pass SECRET --etag-hash --vfs-cache-mode full`

rclone serve webdav --addr :2345 /tmp/qwe --user alice --pass abc123 --vfs-cache-mode full

curl -u USER:SECRET -X PROPFIND LOCAL

curl -u USER:SECRET -O url

curl -u USER:SECRET  -T file url

curl -u USER:SECRET -X DELETE url
