#!/usr/bin/env bash

uvicorn --app-dir Source small_sea_local_hub:app --reload --port ${SMALL_SEA_HUB_PORT}
