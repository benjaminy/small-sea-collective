# Small Sea Manager — Spec

## Overview

Small Sea Manager is a built-in app that is the management interface for all the core Small Sea things:
- Teams
   - Invitations/membership
- Apps
- Service subscriptions
- Devices
- Identity/trust

Small Sea Manager has a special relationship with the [Hub](../../packages/small-sea-hub/spec.md).
For each Team, there is a SQLite database that the Manager updates and the Hub reads to know what resources are available, etc.

## User Interface

Out of the box the Small Sea Manager comes with two primary interfaces:
- Local server web UI
- CLI

## Data Model

The Small Sea Manager keeps a database for each Team.
The NoteToSelf one is extra special.


## Operations

### Create Participant

### Create Team

### Invitations



#### Create Invitation

#### Accept Invitation

### Notification Services

## Sync & Merge

## SQL Schemas

## API Surface
