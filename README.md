# Agent Harness

## 可复现基线

当前仓库使用 Python 3.12。默认 Demo 入口不需要 OpenAI、Slack 或 Google
凭据，用于先验证安装、测试和容器环境。

本地安装与验证：

```bash
poetry env use 3.12
poetry install
cp .env.example .env
make test
make demo
```

访问：

- `http://localhost:8000/health`：健康检查；
- `http://localhost:8000/docs`：FastAPI 文档。

配置模型和 Redis 后，REST Agent 入口为：

```bash
curl -X POST http://localhost:8000/v1/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"message":"查询内部知识库中的 E102 处理流程","session_id":"demo-user"}'
```

默认 `APP_MODE=demo` 不会调用 OpenAI、Google、Slack、Redis 或日志平台；
`/v1/agent/run` 使用确定性 Router 和 Mock Provider，但仍经过正式
Harness、TaskRunner、Runtime、Workspace 与 Trace。

Docker 启动：

```bash
docker compose up --build
```

Docker 默认运行无凭据的 Demo API 和 Redis。真实 Slack Socket Mode 需要在
`.env` 中配置凭据后显式执行：

```bash
make slack
```

常用命令：

```bash
make test              # 单元测试，无外部服务
make test-integration  # 本地基础设施集成测试
make test-live-read    # 外部服务只读探测，结果保存为 PASS/SKIP/FAIL
make test-live-write   # 仅针对显式沙箱目标执行并清理外部写入
make eval              # 真实模型路由评测，需要模型凭据
make demo              # 无凭据启动确定性 Demo API
make demo-scenarios    # 一次运行五个最终场景并保存原始证据
make cli MESSAGE="查询 E102" # 本地 CLI，使用同一个 Harness
make slack             # 真实 Slack 入口，需要外部凭据
```

环境变量模板见 `.env.example`。`credentials.json`、`token.pickle`、`.env`、
运行日志和本地数据库均不得提交。项目最终态验收标准见
[`docs/FINAL_STATE_ACCEPTANCE.md`](docs/FINAL_STATE_ACCEPTANCE.md)。

### 五个无凭据演示场景

一条命令运行全部场景：

```bash
make demo-scenarios
```

结果写入 `logs/demo_scenarios.json`，Workspace 证据写入
`workspaces/demo-cli/`。五个场景分别展示：

1. `knowledge`：FAISS 100 → SVMRank 20 → Cross-Encoder 3 和来源引用；
2. `log_to_task`：日志诊断、`$from` 输出绑定、任务创建与外部 ID 验证；
3. `parallel_reads`：知识与日历两个独立读步骤并行；
4. `safe_delete`：先返回候选和确认要求，再删除并拒绝重复确认；
5. `recovery`：模拟进程中断留下的 `RUNNING` 读步骤并恢复完成。

也可以启动 API 后分别调用：

```bash
curl http://localhost:8000/v1/demo/scenarios
curl -X POST http://localhost:8000/v1/demo/scenarios/knowledge
curl -X POST http://localhost:8000/v1/demo/scenarios/log_to_task
curl -X POST http://localhost:8000/v1/demo/scenarios/parallel_reads
curl -X POST http://localhost:8000/v1/demo/scenarios/safe_delete
curl -X POST http://localhost:8000/v1/demo/scenarios/recovery
```

每个结果都包含响应、完整 Trace 和 `evidence_boundary`。Mock 场景证明 Harness
控制流，不替代 OpenAI、Google、日志平台或 Redis 的 Live Integration。详细讲解
见 [`docs/DEMO.md`](docs/DEMO.md)。

### Live Integration

真实外部服务使用独立命令，不与单元测试或 Mock Demo 混合：

```bash
make eval            # 真实模型 Router 评测
make test-live-read  # Redis、日志、RAG、Google、Slack 的只读/无副作用检查
```

外部写入默认关闭。只有 `.env` 明确提供沙箱 Calendar、Tasklist 或 Slack Channel
ID 后，才运行 `make test-live-write`。Google 临时 Event/Task 会在验证后删除；
Slack 探测消息是外部可见副作用，不应指向正式频道。每项检查保存 `PASS`、
`SKIP` 或 `FAIL`，缺少配置不会被记录成成功。详细配置和证据边界见
[`docs/LIVE_INTEGRATION.md`](docs/LIVE_INTEGRATION.md)。

## 项目简介

聊天助手是一个基于 LangChain 框架开发的智能 Agent，旨在展示如何构建一个具有实际应用价值的 AI 助手。

## 核心功能

- 基础 Agent 交互系统
- 基于 RAG (检索增强生成) 的知识库查询
- 实时在线搜索能力
- Google Calendar 与 Tasks 自然语言交互
- 情绪识别与多轮对话策略

## 核心架构

Slack、REST API 和本地 CLI 都通过同一个 runtime factory 进入 Harness：

```text
Slack / REST API / Local CLI
              ↓
         HarnessAgent
              ↓
      HarnessApplication
              ↓
Router → TaskRunner → SkillRuntime
              ↓
Knowledge / Web / Calendar / Tasks Skills
```

Harness 是所有入口的默认执行路径：

```env
AGENT_RUNTIME=harness
HARNESS_TRACE_PATH=logs/harness_trace.jsonl
```

旧 `AgentClass` 仅作为迁移期的显式回退：`AGENT_RUNTIME=legacy`。

当前注册的真实 Skills：

- `knowledge_search`：FAISS Top-100 → SVMRank Top-20 → Cross-Encoder Top-3，随后基于证据生成答案；
- `log_diagnosis`：查询配置的日志 API 或分析用户提供的日志文本；
- `web_search`：复用现有实时搜索；
- `calendar_management`：查询、创建、修改和安全删除日程；
- `task_management`：创建 Google Tasks 待办。

Harness 统一负责参数校验、Skill 查找、异常隔离、任务依赖、Redis
任务状态和请求级 Trace。旧 `AgentClass` 不再是任何入口的默认运行时。
同一批无依赖读取步骤会在受控线程池中并行执行；默认全局并发上限为 4，
可通过 `HARNESS_MAX_CONCURRENCY` 调整。写操作保持串行。

写操作会保存幂等记录，并且只有外部状态验证通过后才算完成。无法判断外部
结果时步骤进入 `UNKNOWN`，恢复流程只重新验证，不会盲目重复写入。自动重试
仅用于声明为读取操作的 Skill。

### 状态、证据与用户偏好分层

Harness 不把所有数据都塞进 Redis：

| 数据层 | 保存内容 | 默认保留策略 |
| --- | --- | --- |
| Redis Task/Session | 当前步骤、依赖、状态、外部资源 ID 和短期会话 | Task 7 天；Session 24 小时 |
| Workspace | 原始请求、执行计划、每步输入和输出证据 | 本地持久目录，由部署方清理或归档 |
| Redis User Profile | 时区、输出语言、默认日历/任务列表和显式偏好 | 30 天，可续期 |

Workspace 中的 JSON 采用原子写入，并在引用中记录 SHA-256 与字节数。Redis
任务状态只内联不超过 `WORKSPACE_INLINE_MAX_BYTES` 的结果；更大的结果仅保存
`workspace://...` 引用，下游步骤仍能从 Workspace 解析 `$from` 绑定。

用户偏好通过独立接口显式管理，不会从对话中静默推断：

```bash
curl -X PUT http://localhost:8000/v1/users/demo-user/profile \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-user","time_zone":"America/New_York","output_language":"zh-CN"}'

curl http://localhost:8000/v1/users/demo-user/profile
```

调用 Agent 时传入同一个 `user_id`，Profile 会作为系统上下文提供给 Router。
本地 Workspace 默认目录为 `workspaces/`，Docker Compose 已将其挂载为持久目录。

### 三级 RAG

知识库查询的正式链路位于 `src/harness/rag.py`。FAISS 使用归一化向量做
候选召回；SVMRank 使用离线训练的线性权重和低成本特征缩小候选；
Cross-Encoder 将候选组成 query-document pairs 后一次批量评分。最终输出包含
`answer`、可定位的 `sources` 以及每层候选数和耗时 `retrieval_trace`。没有候选
达到 `RAG_RELEVANCE_THRESHOLD` 时直接拒答，不调用生成模型。

安装可选的本地检索/精排依赖：

```bash
poetry install -E rag
```

输入文档为 JSONL，每行必须包含 `chunk_id`、`content`、`source`，可选
`metadata`。使用与在线查询相同的 Embedding 配置建立索引：

```bash
make rag-index \
  INPUT=data/knowledge_chunks.jsonl \
  FAISS_INDEX_PATH=rag_data/knowledge.faiss \
  RAG_DOCUMENTS_PATH=rag_data/documents.jsonl
```

SVMRank 训练数据同样为 JSONL，每行包含 `query_id`、`relevance` 以及
`dense_score`、`token_overlap`、`exact_phrase`、`length_penalty` 四个特征：

```bash
.venv/bin/python scripts/train_svmrank.py \
  --input data/svmrank_training.jsonl \
  --output rag_data/svmrank_weights.json
```

配置 `.env` 中的三个产物路径后，Harness 默认 `knowledge_search` 会加载这条
三级链路。Cross-Encoder 默认使用 CPU；生产环境可将
`CROSS_ENCODER_DEVICE=cuda`。模型下载、真实 FAISS 索引、训练标签与 OpenAI
调用属于 Live Integration，单元测试不会把注入式 Fake 模型结果冒充线上结果。

固定评测集每行格式为
`{"query":"...","relevant_chunk_ids":["chunk-id"]}`。下面的命令会输出
FAISS、FAISS+SVMRank、FAISS+Cross-Encoder 和完整三级检索四组平均
Recall、MRR、NDCG：

```bash
make rag-eval DATASET=evals/rag_cases.jsonl
```

### Trace 与性能报告

每个请求使用同一个 `request_id` 串联请求、路由、任务和 Skill 事件。事件字段
包含 `session_id`、`user_id`、`task_id`、`step_id`、Skill、状态、耗时、重试、
外部资源 ID 和验证结果。用户请求、计划、步骤输入输出和最终响应保存在
Workspace；Trace 只保存带校验和的引用，避免复制大对象或公开用户内容。

`REQUEST_COMPLETED` 记录请求级耗时和结果状态。使用固定数据集完成一次运行后，
生成带实验边界的性能报告：

```bash
make perf-report \
  TRACE=logs/harness_trace.jsonl \
  DATASET=evals/router_cases.jsonl \
  CONCURRENCY=1 \
  WARM_STATE=prewarmed \
  MODEL=gpt-4o-mini \
  OUTPUT=logs/performance_report.json
```

报告包含数据集 SHA-256、请求量、并发度、冷启动/预热状态、模型、硬件、测试
时间、Git commit、P50、P95 和失败率。只有该报告实际生成并保存后才能引用其中
的性能数字。无真实用户数据的事件样例见
`docs/examples/harness_trace.sample.jsonl`。

运行 Harness 聚焦测试：

```bash
make test
```

`src/harness/skills` 是正式 Skill 协议。旧 `src/skills` 只服务于显式启用的
legacy runtime。日志 Skill 已接入 Harness，但真实日志平台可用性需要通过
单独的 Live Integration 验证，不能由 Mock 测试替代。


## 技术栈

- LangChain
- Python 3.9+
- Slack Bolt Framework
- Google Calendar API
- Google Tasks API
- Vector Database
- Emotion Analysis Models




##  安装说明

### 1. 系统要求

- Python 3.9 或更高版本
- Redis Stack 服务器
- Git（用于克隆项目）

### 2. 安装步骤

#### 2.1 安装 Redis Stack

根据您的操作系统选择相应的安装方式：

**MacOS**:

```bash
brew install redis-stack
```

**Ubuntu/Debian**:

```bash
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list
sudo apt-get update
sudo apt-get install redis-stack-server
```

**Windows**:

- 访问 [Redis 下载页面](https://redis.io/download/)
- 下载并安装 Redis Stack

#### 2.2 安装 Python 依赖

使用 Poetry 安装项目依赖：

```bash
# 安装 Poetry（如果未安装）
pip install poetry

# 安装项目依赖
poetry install

# 激活虚拟环境（二选一）：
# 方式1：使用新的 env activate 命令（推荐）
poetry env use python3
source $(poetry env info --path)/bin/activate  # Unix/MacOS
# 或
.\$(poetry env info --path)\Scripts\activate   # Windows

# 方式2：安装并使用 shell 插件
poetry self add poetry-plugin-shell
poetry shell
```

### 3. 环境配置

从无密钥模板创建本地配置：

```bash
cp .env.example .env
```

Demo API 和单元测试不需要外部凭据。只有启用对应 Live Integration 时才填写
OpenAI、Slack、Google 或日志平台配置。Google OAuth 使用的
`credentials.json` 和 `token.pickle` 只能保存在本地，禁止提交到 Git。

## 🔧 使用指南

### 1. 启动服务

#### 1.1 启动 Redis 服务

```bash
# MacOS
redis-stack-server

# Ubuntu/Debian
sudo systemctl start redis-stack-server

# Windows
# 通过安装程序启动 Redis 服务
```

#### 1.2 启动聊天助手

```bash
# 确保虚拟环境已激活（命令行前缀应显示虚拟环境名称）
# 运行主程序
poetry run python -m src.SlackWebHook
```

### 2. Slack 配置

1. 访问 [Slack API 网站](https://api.slack.com/apps)
2. 创建新的 Slack 应用
3. 配置以下权限：
   - `chat:write`
   - `app_mentions:read`
   - `channels:history`
   - `groups:history`
   - `im:history`
   - `mpim:history`
4. 安装应用到工作区
5. 获取并配置以下信息：
   - Bot User OAuth Token (SLACK_BOT_TOKEN)
   - App-Level Token (SLACK_APP_TOKEN)
6. 在需要使用的频道中添加机器人

### 3. Google Calendar 配置

1. 访问 [Google Cloud Console](https://console.cloud.google.com)
2. 创建新项目
3. 启用 Google Calendar API 和 Google Tasks API
4. 创建 OAuth 2.0 凭据
5. 下载凭据文件并重命名为 `credentials.json`
6. 将 `credentials.json` 放在项目根目录
7. 首次运行时，会打开浏览器要求授权访问

### 4. 基本使用

- **知识库查询**：直接向机器人提问，它会从知识库中检索相关信息
- **日程管理**：使用自然语言创建、查询或修改 Google Calendar 日程
- **待办事项**：通过对话方式管理 Google Tasks 待办任务
- **实时搜索**：询问实时信息，机器人会通过搜索引擎获取答案

### 5. 常见问题处理

- **Redis 连接失败**：检查 Redis 服务是否正常运行
- **知识库添加**: 入口在 localhost:8000/docs 中，目前只支持批量添加 url
- **Google API 授权失败**：检查 credentials.json 是否正确配置
- **Slack 连接失败**：检查 Bot Token 和 App Token 是否正确


