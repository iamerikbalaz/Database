import base64
import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import create_app
from app.previews import PreviewError, render_original_preview
from test_previews import POSIX, IDENTITY, make


@POSIX
@pytest.mark.parametrize("extension,mode", [("png","RGBA"),("jpg","RGB"),("webp","RGB"),("tiff","RGB"),("tiff","I;16")])
def test_full_quality_preserves_original_resolution_and_bytes(tmp_path, extension, mode):
    path=make(tmp_path,extension,size=(1200,1200),mode=mode)
    source=Image.new(mode,(1200,1200),32768 if mode=="I;16" else (12,50,200,170) if mode=="RGBA" else (12,50,200)); source.save(path)
    original=path.read_bytes(); modified=path.stat().st_mtime_ns
    result=render_original_preview(tmp_path,(IDENTITY,),path.name,hashlib.sha256(original).hexdigest())
    raw=base64.b64decode(result["data"])
    assert (result["width"],result["height"])==(1200,1200)
    if extension!="tiff": assert raw==original
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if extension in {"png","tiff"}: assert image.getpixel((0,0))==source.getpixel((0,0))
    assert path.read_bytes()==original and path.stat().st_mtime_ns==modified
    with pytest.raises(PreviewError,match="PREVIEW_SOURCE_CHANGED"):
        render_original_preview(tmp_path,(IDENTITY,),path.name,"0"*64)


@pytest.mark.parametrize("name", ["../private.png","C:\\outside.png","folder/image.png","active.svg"])
def test_original_invalid_names_fail_before_filesystem_access(monkeypatch, name):
    monkeypatch.setattr("app.previews._render_preview",lambda *args:pytest.fail("Unexpected source access"))
    with pytest.raises(PreviewError,match="PREVIEW_UNSAFE_NAME"):
        render_original_preview(Path("unused"),(IDENTITY,),name,"a"*64)


@POSIX
def test_original_worker_http_binds_hash_and_never_resizes(tmp_path):
    path=make(tmp_path,size=(1200,1200)); raw=path.read_bytes()
    body={"folder_path":IDENTITY,"name":path.name,"expected_sha256":hashlib.sha256(raw).hexdigest()}
    client=TestClient(create_app(tmp_path)); result=client.post("/internal/material-preview-original",json=body)
    assert result.status_code==200 and result.json()["width"]==1200
    assert base64.b64decode(result.json()["data"])==raw
    assert client.post("/internal/material-preview-original",json={**body,"expected_sha256":"0"*64}).status_code==409


@POSIX
@pytest.mark.parametrize("mode,pixels", [("I;16B",bytes([128,0,255,255])),("I;16L",bytes([0,128,255,255]))])
def test_tiff_retains_unsigned_16bit_values_and_color_profile(tmp_path, mode, pixels):
    path=make(tmp_path,"tiff",size=(2,1)); source=Image.frombytes(mode,(2,1),pixels)
    source.save(path,icc_profile=b"synthetic-color-profile")
    result=render_original_preview(tmp_path,(IDENTITY,),path.name,hashlib.sha256(path.read_bytes()).hexdigest())
    with Image.open(io.BytesIO(base64.b64decode(result["data"]))) as image:
        assert image.getpixel((0,0))==32768 and image.getpixel((1,0))==65535
        assert image.info["icc_profile"]==b"synthetic-color-profile"


@POSIX
def test_original_rechecks_source_binding_after_decode(tmp_path, monkeypatch):
    import app.previews as previews
    path=make(tmp_path,size=(1200,1200)); expected=hashlib.sha256(path.read_bytes()).hexdigest()
    decode=previews._decode
    def changed(fd, **kwargs):
        result=decode(fd,**kwargs); path.write_bytes(b"source changed during decode"); return result
    monkeypatch.setattr(previews,"_decode",changed)
    with pytest.raises(PreviewError,match="PREVIEW_SOURCE_CHANGED"):
        render_original_preview(tmp_path,(IDENTITY,),path.name,expected)
