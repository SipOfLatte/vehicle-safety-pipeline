"""Download NHTSA bulk files and land them in S3 bronze.

Each run checks every dataset's ETag with a HEAD request. NHTSA serves the files
from S3 with single-part uploads, so the ETag is the zip's MD5. A dataset is only
downloaded when its ETag differs from the last one we stored.

Snowflake COPY can't read .zip, so the single .txt inside each zip is re-compressed
to .txt.gz on the way through. The bytes of the .txt are unchanged, and the source
zip's ETag is kept in the object metadata and in the manifest.

S3 layout:
    bronze/<dataset>/load_date=YYYY-MM-DD/<FILE>.txt.gz
    bronze/<dataset>/_latest.json   (manifest of the last successful upload)
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import logging
import shutil
import ssl
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import certifi

BASE_URL = "https://static.nhtsa.gov/odi/ffdd"
DATASETS = {
    "complaints": "cmpl/FLAT_CMPL.zip",
    "recalls_post_2010": "rcl/FLAT_RCL_POST_2010.zip",
    "recalls_pre_2010": "rcl/FLAT_RCL_PRE_2010.zip",
    "investigations": "inv/FLAT_INV.zip",
}
CHUNK = 8 * 1024 * 1024
# python.org builds of Python on macOS ship without CA certificates, so use certifi's bundle everywhere.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

log = logging.getLogger(__name__)


@dataclass
class LoadResult:
    dataset: str
    changed: bool
    etag: str
    s3_key: str | None = None
    zip_bytes: int | None = None


def remote_etag(url: str) -> str:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
        return resp.headers["ETag"].strip('"')


def download(url: str, dest: Path, expected_md5: str) -> int:
    """Stream url to dest and check the MD5 against the ETag. Returns bytes written."""
    md5 = hashlib.md5()
    size = 0
    with urllib.request.urlopen(url, timeout=600, context=SSL_CONTEXT) as resp, dest.open("wb") as out:
        while chunk := resp.read(CHUNK):
            md5.update(chunk)
            out.write(chunk)
            size += len(chunk)
    # A multipart ETag ("<md5>-<parts>") isn't a plain MD5, so it can't be checked this way.
    if "-" not in expected_md5 and md5.hexdigest() != expected_md5:
        raise ValueError(f"MD5 mismatch for {url}: got {md5.hexdigest()}, ETag {expected_md5}")
    return size


def zip_to_gzip(zip_path: Path, gz_path: Path) -> str:
    """Re-compress the single file inside zip_path as gzip. Returns the inner file name."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if len(names) != 1:
            raise ValueError(f"{zip_path.name} should hold one file, found {names}")
        with zf.open(names[0]) as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, CHUNK)
    return names[0]


def read_manifest(s3, bucket: str, dataset: str) -> dict | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"bronze/{dataset}/_latest.json")
    except s3.exceptions.NoSuchKey:
        return None
    return json.loads(obj["Body"].read())


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except s3.exceptions.ClientError:
        return False


def ingest_dataset(s3, bucket: str, dataset: str, load_date: dt.date, workdir: Path) -> LoadResult:
    url = f"{BASE_URL}/{DATASETS[dataset]}"
    etag = remote_etag(url)
    manifest = read_manifest(s3, bucket, dataset)
    # Re-upload if the lifecycle rule expired the last copy, even when the ETag is unchanged.
    if manifest and manifest["etag"] == etag and object_exists(s3, bucket, manifest["s3_key"]):
        log.info("%s unchanged (etag %s), skipping", dataset, etag)
        return LoadResult(dataset, changed=False, etag=etag, s3_key=manifest["s3_key"])

    zip_path = workdir / Path(DATASETS[dataset]).name
    zip_bytes = download(url, zip_path, etag)
    gz_path = zip_path.with_suffix(".txt.gz")
    inner = zip_to_gzip(zip_path, gz_path)
    key = f"bronze/{dataset}/load_date={load_date.isoformat()}/{inner}.gz"
    s3.upload_file(
        str(gz_path), bucket, key,
        ExtraArgs={"Metadata": {"source-url": url, "source-etag": etag}},
    )
    new_manifest = {
        "etag": etag,
        "s3_key": key,
        "load_date": load_date.isoformat(),
        "zip_bytes": zip_bytes,
        "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=bucket, Key=f"bronze/{dataset}/_latest.json",
        Body=json.dumps(new_manifest).encode(), ContentType="application/json",
    )
    zip_path.unlink()
    gz_path.unlink()
    log.info("%s uploaded to s3://%s/%s", dataset, bucket, key)
    return LoadResult(dataset, changed=True, etag=etag, s3_key=key, zip_bytes=zip_bytes)


def run(bucket: str, datasets: list[str], load_date: dt.date, s3=None) -> list[LoadResult]:
    if s3 is None:
        import boto3
        s3 = boto3.client("s3")
    with tempfile.TemporaryDirectory() as tmp:
        return [ingest_dataset(s3, bucket, d, load_date, Path(tmp)) for d in datasets]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--datasets", nargs="+", choices=DATASETS, default=list(DATASETS))
    parser.add_argument("--load-date", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for r in run(args.bucket, args.datasets, args.load_date):
        print(json.dumps(r.__dict__))


if __name__ == "__main__":
    main()
