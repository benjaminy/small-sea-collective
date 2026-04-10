import secrets
import struct
import time


def uuid7() -> bytes:
    """Generate a UUIDv7 (time-ordered, random) as 16 bytes."""
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = secrets.token_bytes(10)

    # Bytes 0-5: 48-bit unix timestamp ms (big-endian)
    # Byte 6: version (0111) + top 4 bits of rand
    # Byte 7: next 8 bits of rand
    # Byte 8: variant (10) + 6 bits of rand
    # Bytes 9-15: 48 bits of rand
    b = struct.pack(">Q", timestamp_ms)[2:]
    b += bytes([(0x70 | (rand_bytes[0] & 0x0F)), rand_bytes[1]])
    b += bytes([0x80 | (rand_bytes[2] & 0x3F)]) + rand_bytes[3:10]
    return b
