#!/usr/bin/env python3
"""Stream the requested GGUF from Hugging Face into the configured R2 bucket.

Credentials are read from .env and never printed. The source is not written to
local disk, so the control machine does not need an extra 29 GB of free space.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import boto3
import requests

SOURCE_URL = (
    "https://huggingface.co/OBLITERATUS/Qwen3.8-27B-OBLITERATED/resolve/main/"
    "Qwen3.8-27B-OBLITERATED-Q8_0.gguf"
)
EXPECTED_SHA256 = "afa839b2fa5bc890e5735031dda2c6239d3b6bba3b6ffa29477cbc14a2e1f221"
CHUNK_SIZE = 32 * 1024 * 1024
MAX_CONCURRENT_PARTS = 8


def env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def main() -> None:
    # SkyPilot forwards configuration as environment variables; local operation
    # additionally reads .env. The latter deliberately wins only when present.
    config = dict(os.environ)
    if Path(".env").exists():
        config.update(env_file(Path(".env")))
    required = ("R2_BUCKET", "R2_ENDPOINT", "R2_MODEL_KEY", "R2_UPLOAD_ACCESS_KEY_ID", "R2_UPLOAD_SECRET_ACCESS_KEY")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise SystemExit(f"Missing values in .env: {', '.join(missing)}")

    client = boto3.client(
        "s3", endpoint_url=config["R2_ENDPOINT"], region_name="auto",
        aws_access_key_id=config["R2_UPLOAD_ACCESS_KEY_ID"],
        aws_secret_access_key=config["R2_UPLOAD_SECRET_ACCESS_KEY"],
    )
    try:
        current = client.head_object(Bucket=config["R2_BUCKET"], Key=config["R2_MODEL_KEY"])
        if current.get("Metadata", {}).get("sha256") == EXPECTED_SHA256:
            print("Verified model already exists in R2; nothing to upload.")
            return
        raise SystemExit("An object with this key already exists but cannot be verified. Choose a new key or remove it manually.")
    except client.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
            raise

    # Create the multipart upload *before* reading Hugging Face. This confirms
    # that the local token can write to the bucket before a 29 GB download starts.
    created = client.create_multipart_upload(
        Bucket=config["R2_BUCKET"], Key=config["R2_MODEL_KEY"],
        Metadata={"sha256": EXPECTED_SHA256},
    )
    upload_id = created["UploadId"]
    parts = []
    sha256 = hashlib.sha256()
    bytes_read = 0
    try:
        print("Streaming the public GGUF from Hugging Face to R2. This can take a while.", flush=True)
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PARTS) as pool:
            in_flight = {}

            def collect(done):
                nonlocal bytes_read
                for future in done:
                    part_number, size = in_flight.pop(future)
                    uploaded = future.result()
                    parts.append({"ETag": uploaded["ETag"], "PartNumber": part_number})
                    bytes_read += size
                    print(f"Transferred {bytes_read / 1024**3:.1f} GiB", flush=True)

            with requests.get(SOURCE_URL, stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                part_number = 1
                while True:
                    chunk = response.raw.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sha256.update(chunk)
                    future = pool.submit(
                        client.upload_part, Bucket=config["R2_BUCKET"],
                        Key=config["R2_MODEL_KEY"], UploadId=upload_id,
                        PartNumber=part_number, Body=chunk,
                    )
                    in_flight[future] = (part_number, len(chunk))
                    part_number += 1
                    if len(in_flight) >= MAX_CONCURRENT_PARTS:
                        done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                        collect(done)
            while in_flight:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                collect(done)

        received_sha256 = sha256.hexdigest()
        if received_sha256 != EXPECTED_SHA256:
            raise RuntimeError(f"Checksum mismatch ({received_sha256})")
        client.complete_multipart_upload(
            Bucket=config["R2_BUCKET"], Key=config["R2_MODEL_KEY"], UploadId=upload_id,
            MultipartUpload={"Parts": sorted(parts, key=lambda part: part["PartNumber"])},
        )
    except BaseException:
        client.abort_multipart_upload(
            Bucket=config["R2_BUCKET"], Key=config["R2_MODEL_KEY"], UploadId=upload_id,
        )
        raise
    print("Upload complete and SHA-256 verified.")


if __name__ == "__main__":
    main()
