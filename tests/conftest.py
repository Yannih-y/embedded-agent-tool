"""测试会话级数据隔离：整个套件用独立临时数据目录，绝不碰真实记忆库。

不隔离的后果（实测复现过）：测试往用户真实的 ~/.agent_memory_pool 写数据，
一是污染真实记忆，二是向量库随历史测试滚雪球——faiss 先取 top-k 再按
user_id 过滤，历史垃圾把 top-k 挤满后过滤归零，用例开始"随机"挂。

conftest 在所有测试模块 import 之前加载，这里设 env 早于
memorypool.config 的 DATA_ROOT import 时快照，对全套件生效。
无条件覆盖（不是 setdefault）：测试永远不该写任何真实数据目录。
需要独立目录的用例（真起进程/自动拉起）再各自 override，互不冲突。
"""

import atexit
import os
import shutil
import tempfile

_session_root = tempfile.mkdtemp(prefix="mempool_test_session_")
os.environ["MEMPOOL_DATA_ROOT"] = _session_root

# 会话结束尽力清掉；Windows 上句柄未释放删不掉也无妨，落在系统临时目录里
atexit.register(shutil.rmtree, _session_root, ignore_errors=True)
