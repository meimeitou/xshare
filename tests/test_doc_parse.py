import base64
import json
import sys
import types

import pytest

from xshare.tools import doc_parse


def _install_fake_mineru(monkeypatch, results=None, raises=None):
    """注入一个假的 mineru_kie_sdk 模块。"""

    class FakeClient:
        def __init__(self, base_url=None, pipeline_id=None, timeout=30):
            pass

        def upload_file(self, file_path):
            return ["file-1"]

        def get_result(self, file_ids, timeout=120, poll_interval=3):
            if raises:
                raise raises
            return results or {"parse": "text", "split": [], "extract": []}

    fake_mod = types.ModuleType("mineru_kie_sdk")
    fake_mod.MineruKIEClient = FakeClient
    monkeypatch.setitem(sys.modules, "mineru_kie_sdk", fake_mod)


@pytest.mark.asyncio
async def test_doc_parse_missing_pipeline_id(monkeypatch, tmp_path):
    _install_fake_mineru(monkeypatch)
    monkeypatch.delenv("MINERU_PIPELINE_ID", raising=False)

    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4")

    resp = await doc_parse.doc_parse({"file_path": str(f)})
    data = json.loads(resp)

    assert "error" in data
    assert "pipeline_id" in data["error"]


@pytest.mark.asyncio
async def test_doc_parse_file_not_found(monkeypatch):
    _install_fake_mineru(monkeypatch)
    monkeypatch.setenv("MINERU_PIPELINE_ID", "pipe-123")

    resp = await doc_parse.doc_parse({"file_path": "/nonexistent/file.pdf"})
    data = json.loads(resp)

    assert "error" in data
    assert "文件不存在" in data["error"]


@pytest.mark.asyncio
async def test_doc_parse_unsupported_type(monkeypatch, tmp_path):
    _install_fake_mineru(monkeypatch)
    monkeypatch.setenv("MINERU_PIPELINE_ID", "pipe-123")

    f = tmp_path / "a.txt"
    f.write_text("hello")

    resp = await doc_parse.doc_parse({"file_path": str(f)})
    data = json.loads(resp)

    assert "error" in data
    assert "不支持的文件类型" in data["error"]


@pytest.mark.asyncio
async def test_doc_parse_pdf_success(monkeypatch, tmp_path):
    _install_fake_mineru(monkeypatch, results={"parse": "hello", "split": ["p1"], "extract": [{"k": "v"}]})
    monkeypatch.setenv("MINERU_PIPELINE_ID", "pipe-123")

    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4")

    resp = await doc_parse.doc_parse({"file_path": str(f)})
    data = json.loads(resp)

    assert data["file"] == "a.pdf"
    assert data["parse"] == "hello"
    assert data["extract"] == [{"k": "v"}]


@pytest.mark.asyncio
async def test_doc_parse_base64_success(monkeypatch, tmp_path):
    _install_fake_mineru(monkeypatch, results={"parse": "img-text"})
    monkeypatch.setenv("MINERU_PIPELINE_ID", "pipe-123")

    content = base64.b64encode(b"\x89PNG fake").decode()

    resp = await doc_parse.doc_parse({
        "file_base64": content,
        "file_name": "scan.png",
    })
    data = json.loads(resp)

    assert data["file"].endswith(".png")
    assert data["parse"] == "img-text"


@pytest.mark.asyncio
async def test_doc_parse_timeout(monkeypatch, tmp_path):
    _install_fake_mineru(monkeypatch, raises=TimeoutError("slow"))
    monkeypatch.setenv("MINERU_PIPELINE_ID", "pipe-123")

    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4")

    resp = await doc_parse.doc_parse({"file_path": str(f)})
    data = json.loads(resp)

    assert "error" in data
    assert "超时" in data["error"]


@pytest.mark.asyncio
async def test_doc_parse_failure(monkeypatch, tmp_path):
    _install_fake_mineru(monkeypatch, raises=RuntimeError("api down"))
    monkeypatch.setenv("MINERU_PIPELINE_ID", "pipe-123")

    f = tmp_path / "a.jpg"
    f.write_bytes(b"fake jpg")

    resp = await doc_parse.doc_parse({"file_path": str(f)})
    data = json.loads(resp)

    assert "error" in data
    assert "解析失败" in data["error"]
