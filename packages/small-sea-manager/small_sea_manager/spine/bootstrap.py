"""Read and extend the Constitution carried in a linked-device Core baseline."""

import time

from sqlalchemy import text

from . import encoding, view


def objects(conn):
    return dict(conn.execute(text("SELECT object_id, encoded FROM spine_object")).all())


def linked_view(held, origin):
    genesis = held.get(origin)
    if genesis is None or encoding.decode_genesis(genesis)["id"] != origin:
        raise ValueError("Core baseline does not contain the expected Constitution origin")
    for key, raw in held.items():
        obj = encoding.decode_object(raw)
        if obj["id"] != key:
            raise ValueError("Constitution object does not match its ID")
        if key != origin and obj["body"]["kind"] == "genesis":
            raise ValueError("Core baseline contains multiple Constitution origins")
        # Replaying a reconciliation needs this device's remembered choices.
        if obj["body"]["kind"] == "reconciliation":
            raise ValueError("Device linking from a reconciled Constitution is not implemented")
    result = view.compute_view(list(held.values()), genesis)
    if (result["classification"] != "followed"
            or any(obj["status"] != "valid" for obj in result["objects"].values())):
        raise ValueError("Device linking requires a complete, valid Constitution with one head")
    return result


def add_device(conn, origin, member, seed, device):
    held = objects(conn)
    result = linked_view(held, origin)
    # The first cutover covers the members in genesis. The existing admission
    # ceremony still records newly invited members in certificates.
    genesis_members = encoding.decode_genesis(held[origin])["body"]["members"]
    if member not in {row["member_id"] for row in genesis_members}:
        return encoding.make_bundle(origin, result["heads"][0], list(held.values()))
    devices = result["acting_state"]["devices"]
    if devices.get(encoding.public_key(seed), {}).get("member_id") != member:
        raise ValueError("Authorizing device has no standing for this member in the Constitution")
    if device in devices:
        if devices[device]["member_id"] != member:
            raise ValueError("Joining device already belongs to another member")
    else:
        event = encoding.make_event(
            seed, origin, "ordinary", result["heads"][0],
            {"op": "add_device", "member_id": member, "device_key": device, "label": ""},
            int(time.time()),
        )
        event_id = encoding.decode_event(event)["id"]
        held[event_id] = event
        result = linked_view(held, origin)
        conn.execute(text("INSERT INTO spine_object VALUES (:id, 'ordinary', :encoded)"),
                     {"id": event_id, "encoded": event})
    return encoding.make_bundle(origin, result["heads"][0], list(held.values()))


def check_bundle(raw, origin, member, authorizer, device):
    bundle = encoding.decode_canonical(raw)
    decoded = encoding.decode_bundle(raw)
    if decoded["origin"] != origin:
        raise ValueError("Bootstrap Constitution names a different origin")
    held = {obj["id"]: bytes.fromhex(encoded)
            for obj, encoded in zip(decoded["objects"], bundle["objects"], strict=True)}
    result = linked_view(held, origin)
    if result["heads"] != [decoded["tip"]]:
        raise ValueError("Bootstrap Constitution does not name its acting head")
    devices = result["acting_state"]["devices"]
    genesis_members = encoding.decode_genesis(held[origin])["body"]["members"]
    if member in {row["member_id"] for row in genesis_members} and any(
        devices.get(key, {}).get("member_id") != member for key in (authorizer, device)
    ):
        raise ValueError("Bootstrap Constitution does not recognize both devices for this member")
    return held
