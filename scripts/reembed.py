"""换 embedding 模型后的全量重嵌入迁移工具。

背景：换 embedding 模型（如 en → zh）后新旧向量空间不同，旧 faiss 索引直接查会
失真，必须全量重嵌入。本脚本：备份 → 从 faiss docstore 导出全部记忆 → 清空
向量库 → 按当前配置（config.EMBED_MODEL）重灌。

用法（先停服务，脚本是迁移期间的唯一写者）：
    kill $(cat ~/.agent_memory_pool/server.pid)   # Windows: taskkill /PID <pid> /F
    .venv/Scripts/python scripts/reembed.py           # 或 uv run python scripts/reembed.py

注意：
- mem0 会给重灌的记忆盖新 created_at（其管理字段不可外部指定），原始时刻保存在
  metadata.orig_created_at，不丢数据；检索展示的相对时间（age）会按新时刻计算
- 记忆 id 会全部更换；池外不应持有旧 id 引用（本项目无此依赖）
"""
from __future__ import annotations

import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memorypool.config import DATA_ROOT, EMBED_MODEL, build_mem0_config  # noqa: E402
from memorypool.daemon import probe  # noqa: E402

FAISS_DIR = Path(DATA_ROOT) / "faiss"
DOCSTORE = FAISS_DIR / "mempool.json"


def main() -> None:
    base = "http://127.0.0.1:8800"
    if probe(base) != "down":
        raise SystemExit(
            f"服务仍在运行（{base}）——先停掉再迁移（单写者铁律）：\n"
            f"  taskkill /PID <server.pid 内容> /F"
        )
    if not DOCSTORE.exists():
        raise SystemExit(f"未找到向量库 docstore：{DOCSTORE}（空池无需迁移）")

    # 1. 备份整个数据目录（zip 放数据目录之外，避免自包含）
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = Path(DATA_ROOT).parent / f"agent_memory_pool-backup-{stamp}.zip"
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in Path(DATA_ROOT).rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(Path(DATA_ROOT)))
    print(f"1. 已备份 -> {backup}")

    # 2. 从 docstore 导出全部记忆（结构：{docstore: {id: payload}, index_to_id: ...}）
    raw = json.loads(DOCSTORE.read_text(encoding="utf-8"))
    payloads = list(raw.get("docstore", {}).values())
    if not payloads:
        raise SystemExit("docstore 为空——若为上次迁移中断所致，先从备份 zip 恢复 faiss/ 再跑")
    # 导出快照独立落盘：重灌若失败，重跑不必解备份 zip
    export_file = Path(DATA_ROOT).parent / f"agent_memory_pool-export-{stamp}.json"
    export_file.write_text(
        json.dumps(payloads, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"2. 导出 {len(payloads)} 条记忆（快照：{export_file}）")

    # 2.5 维度自检：先真嵌入一条，核对与配置一致，防 faiss 维度断言炸在半途
    from memorypool.config import EMBED_DIMS
    from fastembed import TextEmbedding

    probe_vec = next(iter(TextEmbedding(model_name=EMBED_MODEL).embed(["维度自检"])))
    if len(probe_vec) != EMBED_DIMS:
        raise SystemExit(
            f"模型 {EMBED_MODEL} 实际输出 {len(probe_vec)} 维，但配置 EMBED_DIMS={EMBED_DIMS}——"
            f"设置 MEMPOOL_EMBED_DIMS={len(probe_vec)} 或修正 config._MODEL_DIMS 后重跑"
        )
    print(f"2.5 维度自检通过：{EMBED_MODEL} = {EMBED_DIMS} 维")

    # 3. 清空向量库目录（备份已含全部旧文件）
    for f in FAISS_DIR.iterdir():
        f.unlink()
    print("3. 旧向量库已清空")

    # 4. 按当前配置重灌（首次会下载新 embedding 模型）
    from mem0 import Memory

    print(f"4. 以 {EMBED_MODEL} 重建索引...")
    m = Memory.from_config(build_mem0_config())
    ok, skipped = 0, 0
    for p in payloads:
        text = p.get("data") or ""
        user_id = p.get("user_id")
        if not text or not user_id:
            skipped += 1
            continue
        meta = {
            "tier": p.get("tier", "realtime"),
            "consolidated": bool(p.get("consolidated", False)),
        }
        if p.get("created_at"):
            meta["orig_created_at"] = p["created_at"]
        m.add(
            text,
            user_id=user_id,
            agent_id=p.get("agent_id"),
            run_id=p.get("run_id"),
            infer=False,
            metadata=meta,
        )
        ok += 1
    print(f"4. 重灌完成：{ok} 条成功，{skipped} 条跳过（缺 data/user_id）")

    # 5. 计数校验
    new_raw = json.loads(DOCSTORE.read_text(encoding="utf-8"))
    new_count = len(new_raw.get("docstore", {}))
    assert new_count == ok, f"计数不一致：重灌 {ok}，索引里 {new_count}"
    print(f"5. 校验通过：索引 {new_count} 条 == 重灌 {ok} 条")
    print("完成。重启服务即用新模型检索（首次调用自动拉起亦可）。")


if __name__ == "__main__":
    t0 = time.monotonic()
    main()
    print(f"耗时 {time.monotonic() - t0:.1f}s")
