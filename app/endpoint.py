"""Discover a bound loopback endpoint without reserving a second test socket."""

import json
import os
from pathlib import Path


def publish_endpoint(directory, record):
    """Called only while the server owns the runtime lock; never publish credentials."""
    directory = Path(directory)
    temporary = directory / ("endpoint-" + record["instance"] + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(record, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(directory / "endpoint.json")
    # The record may survive exit. Discovery must validate the live health identity.

