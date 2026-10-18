import datetime as dt
import gzip
import hashlib
import io
import json
import types
import zipfile

import pytest

import nhtsa_ingest as ni

LOAD_DATE = dt.date(2026, 10, 19)
TXT = "1\t958241\tVolvo Car USA, LLC\tVOLVO\r\n2\t958130\tFord Motor Company\tFORD\r\n"


class FakeS3:
    """Just enough of the boto3 S3 client for nhtsa_ingest."""

    class NoSuchKey(Exception):
        pass

    class ClientError(Exception):
        pass

    def __init__(self):
        self.objects = {}
        self.exceptions = types.SimpleNamespace(NoSuchKey=self.NoSuchKey, ClientError=self.ClientError)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.ClientError(Key)

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        with open(filename, "rb") as f:
            self.objects[key] = f.read()


@pytest.fixture
def source_zip(tmp_path):
    path = tmp_path / "src" / "FLAT_CMPL.zip"
    path.parent.mkdir()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("FLAT_CMPL.txt", TXT)
    return path


@pytest.fixture
def fake_remote(monkeypatch, source_zip):
    """Serve source_zip as the NHTSA file; its MD5 is the ETag."""
    state = {"etag": hashlib.md5(source_zip.read_bytes()).hexdigest(), "downloads": 0}
    real_download = ni.download

    def fake_download(url, dest, expected_md5):
        state["downloads"] += 1
        return real_download(source_zip.as_uri(), dest, expected_md5)

    monkeypatch.setattr(ni, "remote_etag", lambda url: state["etag"])
    monkeypatch.setattr(ni, "download", fake_download)
    return state


def test_first_run_uploads_gzip_and_manifest(tmp_path, fake_remote):
    s3 = FakeS3()
    [result] = ni.run("bucket", ["complaints"], LOAD_DATE, s3=s3)

    assert result.changed
    assert result.s3_key == "bronze/complaints/load_date=2026-10-19/FLAT_CMPL.txt.gz"
    assert gzip.decompress(s3.objects[result.s3_key]).decode() == TXT
    manifest = json.loads(s3.objects["bronze/complaints/_latest.json"])
    assert manifest["etag"] == fake_remote["etag"]
    assert manifest["s3_key"] == result.s3_key


def test_unchanged_etag_skips_download(fake_remote):
    s3 = FakeS3()
    ni.run("bucket", ["complaints"], LOAD_DATE, s3=s3)
    [result] = ni.run("bucket", ["complaints"], LOAD_DATE + dt.timedelta(days=1), s3=s3)

    assert not result.changed
    assert fake_remote["downloads"] == 1


def test_changed_etag_downloads_again(fake_remote):
    s3 = FakeS3()
    ni.run("bucket", ["complaints"], LOAD_DATE, s3=s3)
    fake_remote["etag"] = "-".join([fake_remote["etag"], "2"])  # multipart-style, skips MD5 check
    [result] = ni.run("bucket", ["complaints"], LOAD_DATE + dt.timedelta(days=1), s3=s3)

    assert result.changed
    assert result.s3_key.startswith("bronze/complaints/load_date=2026-10-20/")


def test_expired_object_is_uploaded_again(fake_remote):
    s3 = FakeS3()
    [first] = ni.run("bucket", ["complaints"], LOAD_DATE, s3=s3)
    del s3.objects[first.s3_key]  # lifecycle rule removed it
    [result] = ni.run("bucket", ["complaints"], LOAD_DATE + dt.timedelta(days=1), s3=s3)

    assert result.changed
    assert fake_remote["downloads"] == 2


def test_download_rejects_md5_mismatch(tmp_path, source_zip):
    with pytest.raises(ValueError, match="MD5 mismatch"):
        ni.download(source_zip.as_uri(), tmp_path / "out.zip", "0" * 32)


def test_zip_with_several_files_is_rejected(tmp_path):
    path = tmp_path / "two.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", "a")
        zf.writestr("b.txt", "b")
    with pytest.raises(ValueError, match="one file"):
        ni.zip_to_gzip(path, tmp_path / "out.gz")
