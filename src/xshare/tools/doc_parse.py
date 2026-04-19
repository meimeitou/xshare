"""文档/图片解析工具 - 基于 MinerU KIE SDK"""

import json
import os
import tempfile
import base64
from pathlib import Path


async def doc_parse(args: dict) -> str:
    """解析文档或图片，提取文字内容"""
    try:
        from mineru_kie_sdk import MineruKIEClient
    except ImportError:
        return json.dumps({"error": "mineru-kie-sdk 未安装，请执行: uv pip install mineru-kie-sdk"}, ensure_ascii=False)

    pipeline_id = args.get("pipeline_id") or os.environ.get("MINERU_PIPELINE_ID", "")
    base_url = args.get("base_url") or os.environ.get("MINERU_API_BASE", "https://mineru.net/api/kie")
    file_path = args.get("file_path", "")
    file_base64 = args.get("file_base64", "")
    file_name = args.get("file_name", "upload.pdf")
    timeout = args.get("timeout", 120)

    if not pipeline_id:
        return json.dumps({
            "error": "缺少 pipeline_id，请在 .env 中设置 MINERU_PIPELINE_ID 或在参数中传入",
        }, ensure_ascii=False)

    # 处理 base64 输入（来自微信图片等）
    tmp_file = None
    if file_base64 and not file_path:
        suffix = Path(file_name).suffix or ".png"
        tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_file.write(base64.b64decode(file_base64))
        tmp_file.close()
        file_path = tmp_file.name

    if not file_path or not Path(file_path).exists():
        return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

    # 检查文件类型
    suffix = Path(file_path).suffix.lower()
    supported = {".pdf", ".jpg", ".jpeg", ".png"}
    if suffix not in supported:
        return json.dumps({
            "error": f"不支持的文件类型: {suffix}，支持: {', '.join(supported)}",
        }, ensure_ascii=False)

    try:
        client = MineruKIEClient(
            base_url=base_url,
            pipeline_id=pipeline_id,
            timeout=30,
        )

        file_ids = client.upload_file(file_path)

        results = client.get_result(
            file_ids=file_ids,
            timeout=timeout,
            poll_interval=3,
        )

        # 整理输出
        output = {
            "file": Path(file_path).name,
            "parse": results.get("parse"),
            "split": results.get("split"),
            "extract": results.get("extract"),
        }

        return json.dumps(output, ensure_ascii=False, default=str)

    except TimeoutError:
        return json.dumps({"error": f"解析超时（{timeout}s），文档可能过大"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"解析失败: {str(e)}"}, ensure_ascii=False)
    finally:
        # 清理临时文件
        if tmp_file:
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass
