# -----------------------------------------------------------------------------
# agent_memory_pool 一键部署（Windows）
#
# 新设备用法：
#   git clone https://github.com/Yannih-y/embedded-agent-tool.git
#   cd embedded-agent-tool
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 `
#       -NestworkRemote https://github.com/<you>/nestwork-private.git
#
# 做的事（全部幂等，重复跑安全）：
#   1. 建 venv 装依赖（优先 uv，退回 pip -e .）
#   2. 生成 ~/.agent_memory_pool/.env 模板（key 可选，只有 LLM 固化链路需要）
#   3. 自动检测本机装了哪些 agent 工具，逐个注册 MCP：
#      Cursor / Claude Code / Codex / Windsurf / Kiro
#   4. （可选）克隆 nestwork 慢记忆仓库并给上述工具装启动注入
#   5. 注册登录自启（HKCU Run，pythonw 无窗口）：开机即在线，
#      AgentClaw 等启动时连 /mcp 的 HTTP 客户端不依赖"谁先调用谁拉起"
#   6. 冒烟测试：客户端自动拉起守护进程并 health 检查
# -----------------------------------------------------------------------------
param(
    [string] $NestworkRemote = "",
    [string] $NestworkPath = "$env:USERPROFILE\nestwork",
    [switch] $SkipMcp,
    [switch] $SkipAutostart,
    [switch] $SkipSmoke
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  v $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "  - $msg" -ForegroundColor DarkGray }

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Summary = New-Object System.Collections.Generic.List[string]

# --- 1. 前置检查 + venv -------------------------------------------------------
Write-Step "环境检查与依赖安装 ($RepoRoot)"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "缺 git，先装 Git for Windows" }

$HasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
if ($HasUv) {
    Push-Location $RepoRoot
    try { uv sync | Out-Null } finally { Pop-Location }
    Write-Ok "uv sync 完成"
} else {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "缺 python（>=3.10），或先装 uv" }
    if (-not (Test-Path "$RepoRoot\.venv")) { python -m venv "$RepoRoot\.venv" }
    & "$RepoRoot\.venv\Scripts\python.exe" -m pip install -q -e $RepoRoot
    Write-Ok "pip install -e . 完成"
}
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { throw "venv python 不存在：$VenvPython" }
$Summary.Add("venv        : $VenvPython")

# --- 2. .env 模板 -------------------------------------------------------------
Write-Step "全局配置 ~/.agent_memory_pool/.env"
$EnvDir = "$env:USERPROFILE\.agent_memory_pool"
$EnvFile = Join-Path $EnvDir ".env"
if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType Directory -Force -Path $EnvDir | Out-Null
    @"
# agent_memory_pool 全局配置（每次启动自动加载，改完重启守护进程生效）
# 纯读写记忆不需要任何 key；只有 LLM 固化/整合链路需要。
# ANTHROPIC_API_KEY=
# ANTHROPIC_AUTH_TOKEN=
# ANTHROPIC_BASE_URL=
# MEMPOOL_PORT=8800
"@ | Set-Content -Path $EnvFile -Encoding UTF8
    Write-Ok "已生成模板（从旧设备拷贝同名文件可直接覆盖）"
} else {
    Write-Skip ".env 已存在，不动"
}

# --- 3. MCP 注册 --------------------------------------------------------------
function Add-McpServer {
    param([string]$Path, [string]$Name, [hashtable]$Entry)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $root = $null
    if (Test-Path $Path) {
        $raw = Get-Content $Path -Raw
        if ($raw -and $raw.Trim()) { $root = $raw | ConvertFrom-Json }
    }
    if (-not $root) { $root = New-Object PSObject }
    if (-not ($root.PSObject.Properties.Name -contains 'mcpServers')) {
        $root | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object PSObject)
    }
    # 幂等守卫：已有同名同 command 的注册就不重写文件（避免无谓 JSON 往返）
    $existing = $root.mcpServers.PSObject.Properties[$Name]
    if ($existing -and $existing.Value.command -eq $Entry.command) { return }
    $entryObj = $Entry | ConvertTo-Json -Depth 10 | ConvertFrom-Json
    $root.mcpServers | Add-Member -NotePropertyName $Name -NotePropertyValue $entryObj -Force
    $json = $root | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

$StdEntry = @{ command = $VenvPython; args = @('-m', 'memorypool.mcp_server') }

if (-not $SkipMcp) {
    Write-Step "MCP 注册（按检测到的工具）"

    # Cursor
    if (Test-Path "$env:USERPROFILE\.cursor") {
        Add-McpServer -Path "$env:USERPROFILE\.cursor\mcp.json" -Name 'agent-memory-pool' -Entry $StdEntry
        Write-Ok "Cursor  -> ~/.cursor/mcp.json"; $Summary.Add("Cursor      : MCP 已注册")
    } else { Write-Skip "Cursor 未检测到" }

    # Claude Code（优先 CLI，保证写进 user scope）
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        $mcpList = ""
        try { $mcpList = (& claude mcp list 2>$null) -join "`n" } catch {}
        if ($mcpList -notmatch 'agent-memory-pool') {
            & claude mcp add --scope user agent-memory-pool -- $VenvPython -m memorypool.mcp_server | Out-Null
        }
        Write-Ok "Claude Code -> claude mcp add (user scope)"; $Summary.Add("Claude Code : MCP 已注册")
    } elseif (Test-Path "$env:USERPROFILE\.claude.json") {
        Add-McpServer -Path "$env:USERPROFILE\.claude.json" -Name 'agent-memory-pool' -Entry $StdEntry
        Write-Ok "Claude Code -> ~/.claude.json（未找到 claude CLI，直接写配置）"; $Summary.Add("Claude Code : MCP 已注册")
    } else { Write-Skip "Claude Code 未检测到" }

    # Codex（TOML，文本级幂等追加）
    if (Test-Path "$env:USERPROFILE\.codex") {
        $codexCfg = "$env:USERPROFILE\.codex\config.toml"
        $toml = ""
        if (Test-Path $codexCfg) { $toml = Get-Content $codexCfg -Raw }
        if ($toml -notmatch '\[mcp_servers\.agent_memory_pool\]') {
            $block = @"

[mcp_servers.agent_memory_pool]
type = "stdio"
command = '$VenvPython'
args = ["-m", "memorypool.mcp_server"]
startup_timeout_sec = 120
"@
            Add-Content -Path $codexCfg -Value $block -Encoding UTF8
        }
        Write-Ok "Codex   -> ~/.codex/config.toml"; $Summary.Add("Codex       : MCP 已注册")
    } else { Write-Skip "Codex 未检测到" }

    # Windsurf
    if (Test-Path "$env:USERPROFILE\.codeium\windsurf") {
        Add-McpServer -Path "$env:USERPROFILE\.codeium\windsurf\mcp_config.json" -Name 'agent-memory-pool' -Entry $StdEntry
        Write-Ok "Windsurf -> ~/.codeium/windsurf/mcp_config.json"; $Summary.Add("Windsurf    : MCP 已注册")
    } else { Write-Skip "Windsurf 未检测到" }

    # Kiro（用户级，全工作区生效）
    if ((Test-Path "$env:USERPROFILE\.kiro") -or (Test-Path "$env:APPDATA\kiro")) {
        $kiroEntry = @{
            command = $VenvPython; args = @('-m', 'memorypool.mcp_server')
            disabled = $false; autoApprove = @('search_memory', 'add_memory')
        }
        Add-McpServer -Path "$env:USERPROFILE\.kiro\settings\mcp.json" -Name 'agent-memory-pool' -Entry $kiroEntry
        Write-Ok "Kiro    -> ~/.kiro/settings/mcp.json"; $Summary.Add("Kiro        : MCP 已注册")
    } else { Write-Skip "Kiro 未检测到" }
}

# --- 4. nestwork 慢记忆（可选）-------------------------------------------------
if ($NestworkRemote -or (Test-Path $NestworkPath)) {
    Write-Step "nestwork 慢记忆 ($NestworkPath)"
    if (-not (Test-Path $NestworkPath)) {
        git clone $NestworkRemote $NestworkPath
        Write-Ok "已克隆 $NestworkRemote"
    } else { Write-Skip "目录已存在，跳过克隆" }

    # nestwork 安装器依赖 python3；Windows 上 python3 常是 WindowsApps 假名，起个会话别名兜底
    Set-Alias -Name python3 -Value python -Scope Script -ErrorAction SilentlyContinue

    $inst = Join-Path $NestworkPath "scripts\install"
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        & (Join-Path $inst "claude.ps1"); $Summary.Add("Claude Code : nestwork 已装")
    }
    if (Test-Path "$env:USERPROFILE\.codex") {
        & (Join-Path $inst "codex.ps1"); $Summary.Add("Codex       : nestwork 已装")
    }
    if (Test-Path "$env:USERPROFILE\.codeium\windsurf") {
        & (Join-Path $inst "generic.ps1") windsurf "$env:USERPROFILE\.codeium\windsurf\memories\global_rules.md"
        $Summary.Add("Windsurf    : nestwork 已装")
    }
    if ((Test-Path "$env:USERPROFILE\.kiro") -or (Test-Path "$env:APPDATA\kiro")) {
        & (Join-Path $inst "generic.ps1") kiro "$env:USERPROFILE\.kiro\steering\AGENTS.md"
        $Summary.Add("Kiro        : nestwork 已装")
    }

    # Claude Code hooks 写的是裸 "bash"，Windows 上会命中 WSL bash；补丁成 Git Bash 绝对路径
    $claudeSettings = "$env:USERPROFILE\.claude\settings.json"
    if (Test-Path $claudeSettings) {
        $gitRoot = Split-Path (Split-Path (Get-Command git).Source)
        $gitBash = $null
        foreach ($cand in @((Join-Path $gitRoot 'bin\bash.exe'), (Join-Path (Split-Path $gitRoot) 'bin\bash.exe'))) {
            if (Test-Path $cand) { $gitBash = $cand; break }
        }
        if ($gitBash) {
            $fwd = $gitBash -replace '\\', '/'
            $raw = Get-Content $claudeSettings -Raw
            $patched = $raw -replace '("command":\s*")bash ', ('${1}' + $fwd + ' ')
            if ($patched -ne $raw) {
                [IO.File]::WriteAllText($claudeSettings, $patched, (New-Object System.Text.UTF8Encoding($false)))
                Write-Ok "已修补 Claude hooks 的 bash 路径 -> $fwd"
            }
        }
    }
} else {
    Write-Skip "未指定 -NestworkRemote 且本机无 $NestworkPath，跳过慢记忆层"
}

# --- 5. 登录自启（HKCU Run，pythonw 无窗口，幂等覆盖） ---------------------------
if (-not $SkipAutostart) {
    Write-Step "注册登录自启"
    $PythonW = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
    if (Test-Path $PythonW) {
        Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
            -Name 'AgentMemoryPool' -Value "`"$PythonW`" -m memorypool.daemon"
        Write-Ok "HKCU Run\AgentMemoryPool -> pythonw -m memorypool.daemon（已就绪则幂等退出）"
        $Summary.Add("autostart   : HKCU Run\AgentMemoryPool（登录即在线，AgentClaw 等启动时连 /mcp 不掉线）")
    } else {
        Write-Skip "venv 无 pythonw.exe，跳过自启（IDE 侧 stdio MCP 首次调用仍会自动拉起）"
    }
}

# --- 6. 冒烟测试 ---------------------------------------------------------------
if (-not $SkipSmoke) {
    Write-Step "冒烟测试（首次运行会下载本地 embedding 模型，可能要 1-2 分钟）"
    # health() 是纯探活不拉起，必须先 ensure_service（新机器上服务必然没起）
    & $VenvPython -c "from memorypool.daemon import ensure_service; from memorypool.client_sdk import MemoryPoolClient; import json; ensure_service('http://127.0.0.1:8800'); print(json.dumps(MemoryPoolClient().health(), ensure_ascii=False))"
    Write-Ok "守护进程拉起 + health 通过"
}

# --- 7. 汇总 -------------------------------------------------------------------
Write-Step "部署完成"
$Summary | ForEach-Object { Write-Host "  $_" }
Write-Host @"

手动收尾（只需一次）：
  1. 如需 LLM 固化功能：填 $EnvFile 里的 key（或从旧设备拷贝整个文件）
  2. 重启各 agent 工具让 MCP 生效（Kiro 热加载可不重启）
  3. 记忆约定：同一个人在所有工具统一用同一个 user_id，跨工具记忆才互通
"@
