"""0.4.0 markdown 导出 + git 同步：渲染、双护栏、冲突隔离、run_id 过滤。

git 场景用「本地 bare 仓库当 remote」，不依赖网络。
"""
import subprocess
from pathlib import Path

import pytest

from memorypool.exporter import (
    ExportBlocked,
    export_memories,
    render_markdown,
)


def _mem(text, agent="cursor", run=None, tier="realtime", at="2026-08-12T06:00:00Z"):
    m = {
        "memory": text,
        "agent_id": agent,
        "created_at": at,
        "metadata": {"tier": tier},
    }
    if run:
        m["run_id"] = run
    return m


def _git(repo: Path, *args):
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, f"git {args} 失败: {r.stderr}"
    return r.stdout


@pytest.fixture()
def repo(tmp_path):
    """工作仓库 + 本地 bare remote（push/pull 全离线）。"""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], capture_output=True, check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(bare), str(work)], capture_output=True, check=True)
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    # 空仓库先落一个初始提交，pull --rebase 才有 HEAD 可用
    (work / "README.md").write_text("init\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "-u", "origin", "HEAD")
    return work


# ---------------------------------------------------------------- 渲染

def test_render_sections_and_header():
    md = render_markdown(
        [
            _mem("实时一条", at="2026-08-12T01:00:00Z"),
            _mem("长期一条", tier="longterm", at="2026-08-11T00:00:00Z"),
            _mem("带run", run="run-x", at="2026-08-12T02:00:00Z"),
        ],
        "alice",
        "2026-08-12T09:00:00Z",
    )
    assert "READ-ONLY" in md and "last-exported-at: 2026-08-12T09:00:00Z" in md
    assert "## 长期记忆（longterm）" in md and "## 实时记忆（realtime）" in md
    assert "长期一条" in md and "实时一条" in md
    assert "run:run-x" in md
    # 迁移场景：orig_created_at 优先展示
    md2 = render_markdown(
        [{"memory": "m", "created_at": "2026-08-12T08:00:00Z",
          "metadata": {"tier": "realtime", "orig_created_at": "2026-08-01T00:00:00Z"}}],
        "alice", "2026-08-12T09:00:00Z",
    )
    assert "2026-08-01T00:00:00Z" in md2


# ---------------------------------------------------------------- 主流程

def test_first_export_commits_and_pushes(repo):
    r = export_memories([_mem("第一条")], "alice", repo)
    assert r.status == "exported"
    target = repo / "memory/pool/alice.md"
    assert target.exists() and "第一条" in target.read_text(encoding="utf-8")
    assert "mempool: 导出 alice" in _git(repo, "log", "-1", "--format=%s")
    # push 真的到了 remote
    assert "memory/pool/alice.md" in _git(repo, "ls-tree", "-r", "--name-only", "origin/master")


def test_second_export_no_change_skips_commit(repo):
    export_memories([_mem("同一条")], "alice", repo)
    head = _git(repo, "rev-parse", "HEAD").strip()
    r = export_memories([_mem("同一条")], "alice", repo)
    assert r.status == "no_change"
    assert _git(repo, "rev-parse", "HEAD").strip() == head  # 没有新提交


def test_dirty_worktree_blocks_export(repo):
    export_memories([_mem("v1")], "alice", repo)
    target = repo / "memory/pool/alice.md"
    target.write_text("人工未提交改动\n", encoding="utf-8")
    with pytest.raises(ExportBlocked, match="未提交"):
        export_memories([_mem("v2")], "alice", repo)


def test_committed_manual_edit_moved_to_edited(repo):
    export_memories([_mem("v1")], "alice", repo)
    target = repo / "memory/pool/alice.md"
    target.write_text("人工改动已提交\n", encoding="utf-8")
    _git(repo, "add", "--", "memory/pool/alice.md")
    _git(repo, "commit", "-m", "manual edit")
    r = export_memories([_mem("v2")], "alice", repo)
    assert r.status == "exported" and r.edited_moved
    edited = Path(r.edited_moved)
    assert edited.exists() and "人工改动已提交" in edited.read_text(encoding="utf-8")
    assert "v2" in target.read_text(encoding="utf-8")


def test_two_machine_conflict_isolated(repo, tmp_path):
    """双机冲突：机器2 先推 → 机器1 pull 冲突 → 主文件取远端、本机版进 .conflicts/。"""
    export_memories([_mem("机器1第一版")], "alice", repo)

    # 机器2：另一份克隆，改同一文件并先推
    work2 = tmp_path / "work2"
    remote_url = _git(repo, "remote", "get-url", "origin").strip()
    subprocess.run(["git", "clone", remote_url, str(work2)], capture_output=True, check=True)
    _git(work2, "config", "user.email", "m2@t")
    _git(work2, "config", "user.name", "m2")
    f2 = work2 / "memory/pool/alice.md"
    f2.write_text("机器2的导出版本\n", encoding="utf-8")
    _git(work2, "add", "--", "memory/pool/alice.md")
    _git(work2, "commit", "-m", "mempool: 导出 alice (machine2)")
    _git(work2, "push")

    # 机器1 本地也有一个未推的导出提交 → 触发 pull --rebase 冲突路径
    f1 = repo / "memory/pool/alice.md"
    f1.write_text("机器1本地新版\n", encoding="utf-8")
    _git(repo, "add", "--", "memory/pool/alice.md")
    _git(repo, "commit", "-m", "mempool: 导出 alice (machine1 local)")

    r = export_memories([_mem("机器1池内容")], "alice", repo)
    assert r.status == "conflict_isolated"
    assert r.conflict_file and Path(r.conflict_file).exists()
    assert "机器1池内容" in Path(r.conflict_file).read_text(encoding="utf-8")
    # 主文件保留远端（机器2）版本
    assert "机器2的导出版本" in f1.read_text(encoding="utf-8")
    # 冲突隔离提交已推送
    assert ".conflicts" in _git(repo, "ls-tree", "-r", "--name-only", "origin/master")


def test_non_git_dir_blocked(tmp_path):
    with pytest.raises(ExportBlocked, match="git 仓库"):
        export_memories([_mem("x")], "alice", tmp_path / "plain")
