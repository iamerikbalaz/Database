import base64
import hashlib
import io
import json
import os

import httpx
import pytest
from PIL import Image
from app.db.models import InternalUser, PBRMaterial

from app.local_materials import LocalMaterialLibrary
from app.preview_client import PreviewClientError, WorkerPreviewClient
from test_application_access import access_case
from test_previews import preview_case, original_payload, ORIGINAL
from test_material_operations import StreamingResponse


@pytest.mark.parametrize("role,code", [(None,401),("PROCESSOR",200),("OTHER",404),("LEADERSHIP",200),("PRODUCTION_LEAD",200),("ADMIN",200)])
def test_original_route_requires_material_access_and_discloses_only_valid_pixels(preview_case, role, code):
    case, worker, path = preview_case
    with case.client(role) as client:
        response = client.get(path + "/preview-original", params={"name":"Synthetic preview.png", "expected_sha256":hashlib.sha256(ORIGINAL).hexdigest()})
        assert response.status_code == code
        if code == 200:
            assert response.content == ORIGINAL and response.headers["Content-Type"] == "image/png"
            assert response.headers["X-Preview-Width"] == "1200" and response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        else: assert not worker.calls


def test_worker_original_transport_pins_the_folder_name_hash_and_full_dimensions(monkeypatch):
    value = original_payload()
    def stream(method, url, **options):
        assert url.endswith("/internal/material-preview-original")
        assert options["json"] == {"folder_path":"SAFE_0001_G03", "name":"Synthetic preview.png", "expected_sha256":value["source_sha256"]}
        assert options["follow_redirects"] is False and options["trust_env"] is False
        return StreamingResponse([json.dumps(value).encode()])
    monkeypatch.setattr(httpx,"stream",stream)
    result = WorkerPreviewClient("http://worker").original("SAFE_0001_G03", "Synthetic preview.png", value["source_sha256"])
    assert result.image_bytes() == ORIGINAL and (result.width,result.height) == (1200,1200)


@pytest.mark.parametrize("change", [{"width":32769},{"width":8192,"height":8192},{"media_type":"image/svg+xml"},{"folder_name":"OTHER"},{"source_sha256":"a"*64},{"name":"../outside.png"}])
def test_original_worker_result_rejects_unbounded_or_unbound_pixels(monkeypatch, change):
    value = original_payload(); expected=value["source_sha256"]; value.update(change)
    monkeypatch.setattr(httpx,"stream",lambda *args,**kwargs:StreamingResponse([json.dumps(value).encode()]))
    with pytest.raises(PreviewClientError):
        WorkerPreviewClient("http://worker").original("SAFE_0001_G03", "Synthetic preview.png", expected)


def test_role_change_during_source_read_rechecks_processor_folder_binding(preview_case):
    case, worker, path = preview_case
    with case.database.session() as session:
        material=session.get(PBRMaterial,case.materials[0].id)
        material.assigned_processor_id=case.users["ADMIN"].id
        material.folder_path="library/OTHER_0001_G03"
        session.commit()
    def change_role():
        with case.database.session() as session:
            session.get(InternalUser,case.users["ADMIN"].id).role="PROCESSOR"; session.commit()
    worker.callback=change_role
    with case.client("ADMIN") as client:
        response=client.get(path+"/preview-original",params={"name":"Synthetic preview.png","expected_sha256":hashlib.sha256(ORIGINAL).hexdigest()})
        assert response.status_code==404 and ORIGINAL not in response.content


@pytest.mark.skipif(os.name != "nt", reason="Native Windows preview filesystem")
@pytest.mark.parametrize("mode,pixels", [("I;16B",bytes([128,0,255,255])),("I;16L",bytes([0,128,255,255]))])
def test_local_tiff_retains_unsigned_16bit_samples_and_color_profile(tmp_path, mode, pixels):
    folder="Brand/BRAND_0001_SAMPLE_K03"; directory=tmp_path/"library"/folder/"PREVIEW"; directory.mkdir(parents=True)
    path=directory/"SPHERE_1.tiff"
    source=Image.frombytes(mode,(2,1),pixels); source.save(path,icc_profile=b"synthetic-color-profile")
    library=LocalMaterialLibrary(tmp_path/"library",tmp_path/"journal")
    result=library.previews.original(folder,path.name,hashlib.sha256(path.read_bytes()).hexdigest())
    with Image.open(io.BytesIO(result.image_bytes())) as image:
        assert image.getpixel((0,0))==32768 and image.getpixel((1,0))==65535
        assert image.info["icc_profile"]==b"synthetic-color-profile"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows preview filesystem")
def test_local_original_holds_read_binding_until_decoding_finishes(tmp_path, monkeypatch):
    folder="Brand/BRAND_0001_SAMPLE_K03"; directory=tmp_path/"library"/folder/"PREVIEW"; directory.mkdir(parents=True)
    path=directory/"SPHERE_1.png"; Image.new("RGB",(16,16)).save(path); original=path.read_bytes()
    library=LocalMaterialLibrary(tmp_path/"library",tmp_path/"journal")
    decode=library.previews._original_bytes; checked=[]
    def during_decode(*args):
        for mutation in (lambda:path.write_bytes(b"replaced"),lambda:path.rename(path.with_name("renamed.png")),lambda:directory.rename(directory.with_name("moved"))):
            with pytest.raises(PermissionError): mutation()
            checked.append(True)
        return decode(*args)
    monkeypatch.setattr(library.previews,"_original_bytes",during_decode)
    assert library.previews.original(folder,path.name,hashlib.sha256(original).hexdigest()).image_bytes()==original
    assert len(checked)==3 and path.read_bytes()==original


@pytest.mark.skipif(os.name != "nt", reason="Native Windows preview filesystem")
@pytest.mark.parametrize("extension,mode", [("png","RGBA"),("jpg","RGB"),("webp","RGB"),("tiff","RGB"),("tiff","I;16")])
def test_local_original_preserves_pixels_and_source_bytes(tmp_path, extension, mode):
    folder="Brand/BRAND_0001_SAMPLE_K03"; directory=tmp_path/"library"/folder/"PREVIEW"; directory.mkdir(parents=True)
    source=Image.new(mode,(1200,1200), 32768 if mode=="I;16" else (25,70,120,160) if mode=="RGBA" else (25,70,120))
    path=directory/f"SPHERE_1.{extension}"; source.save(path)
    original=path.read_bytes(); modified=path.stat().st_mtime_ns
    library=LocalMaterialLibrary(tmp_path/"library", tmp_path/"journal")
    result=library.previews.original(folder,path.name,hashlib.sha256(original).hexdigest())
    assert (result.width,result.height)==(1200,1200)
    assert result.image_bytes() == original if extension != "tiff" else result.media_type == "image/png"
    with Image.open(io.BytesIO(result.image_bytes())) as image:
        image.load()
        if extension in {"png","tiff"}: assert image.getpixel((0,0))==source.getpixel((0,0))
    assert path.read_bytes()==original and path.stat().st_mtime_ns==modified
    with pytest.raises(PreviewClientError, match="could not be verified"):
        library.previews.original(folder,path.name,"0"*64)
    assert not library.previews.cache


@pytest.mark.skipif(os.name != "nt", reason="Native Windows preview filesystem")
@pytest.mark.parametrize("defect", ["truncated","format","animated","pixel-limit"])
def test_local_original_rejects_invalid_preview_sources(tmp_path, defect):
    folder="Brand/BRAND_0001_SAMPLE_K03"; directory=tmp_path/"library"/folder/"PREVIEW"; directory.mkdir(parents=True)
    path=directory/"SPHERE_1.png"; Image.new("RGB",(16,16)).save(path)
    if defect=="truncated": path.write_bytes(path.read_bytes()[:30])
    elif defect=="format": Image.new("RGB",(16,16)).save(path,format="JPEG")
    elif defect=="animated": Image.new("RGB",(16,16),"red").save(path,save_all=True,append_images=[Image.new("RGB",(16,16),"blue")])
    else: Image.new("L",(8192,4097)).save(path)
    library=LocalMaterialLibrary(tmp_path/"library",tmp_path/"journal")
    with pytest.raises(PreviewClientError): library.previews.original(folder,path.name,hashlib.sha256(path.read_bytes()).hexdigest())
