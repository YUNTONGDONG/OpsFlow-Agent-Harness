# Agent Harness 最终态验收标准

## 1. 文档目的

本文档定义本项目从当前原型演进到简历最终态时必须满足的验收标准。
后续实现、测试、演示和 GitHub 发布均以本文档为统一基线。

项目的最终目标是提供一个可以从 GitHub 克隆、按文档直接启动、能够展示真实
Agent 执行过程，并且具备测试、评测、Trace 和可复现实验结果的完整项目。

## 2. 最终态系统边界

最终主链路为：

```text
Slack / Local Demo / REST API
              ↓
      FastAPI + Adapter
              ↓
      Agent Harness
  Router → Planner → TaskRunner → Runtime
              ↓
            Skills
  Knowledge / Log Diagnosis / Calendar / Tasks
```

配套组件：

- Redis：保存 Session、Task、Step、幂等记录和外部对象 ID；
- Workspace：保存原始日志、检索证据、工具材料和步骤输出；
- Trace：记录路由、计划、执行、延迟、Token、错误和验证结果；
- LLM：负责请求理解、规划和基于证据的内容生成；
- Harness：负责状态、依赖、执行控制、恢复和安全边界；
- Skill：负责具体业务能力及外部状态验证。

核心责任边界：

> 模型负责理解和规划，Harness 负责状态和执行控制，Skill 负责具体业务操作。

## 3. 统一完成定义

某项能力只有同时满足以下条件，才可以标记为“已完成”：

1. 代码已经接入默认 Harness 主链路，不是孤立脚本或未注册模块；
2. 输入和输出具有明确 Schema；
3. 关键成功、失败和边界行为具有单元测试；
4. 涉及基础设施时具有可重复的集成测试；
5. 涉及真实外部服务时具有单独记录的 Live Integration 结果；
6. Trace 可以还原该能力的关键执行过程；
7. README 或对应文档提供可执行的复现命令；
8. 测试结论能够追溯到测试环境、原始输出和 Git commit。

以下证据不能相互替代：

- 单元测试通过不等于真实外部服务联调通过；
- Mock/Fake Backend 通过不等于 Live Integration 通过；
- 路由正确不等于 Skill 执行成功；
- Skill 返回成功不等于外部状态已经验证成功；
- 固定预热测试的性能数据不代表通用线上 SLA。

## 4. 必须通过的最终 Demo 场景

### 场景 A：带证据的知识库问答

示例请求：

```text
从内部知识库查一下 E102 错误怎么处理？
```

预期执行：

```text
Router
→ Knowledge Skill
→ FAISS Top-100
→ SVMRank Top-20
→ BERT Cross-Encoder Top-3
→ LLM 基于证据回答
```

验收条件：

- Router 只选择 `knowledge_search`；
- 返回答案以及可定位的来源文档或 Chunk；
- Trace 记录三层检索的候选数量和各阶段耗时；
- 最终回答只能使用选中的证据；
- 没有相关证据时明确拒答，不生成无依据答案；
- 能运行 FAISS、FAISS + SVMRank、FAISS + Cross-Encoder 和完整三级检索的消融评测；
- CPU 环境可以运行缩小版 Demo，GPU 仅作为可选加速。

所需证据：

- 单元测试；
- 固定数据集上的 Recall、MRR 或 NDCG；
- 一次可复现的端到端查询；
- 示例 Trace 和最终回答。

### 场景 B：日志诊断后创建任务

示例请求：

```text
检查 payment-service 最近一小时的错误日志；如果发现 E102，创建一个修复任务。
```

预期计划：

```text
step_1: log_diagnosis
step_2: task_management, depends_on=[step_1]
```

验收条件：

- Planner 生成合法的有向无环图；
- `log_diagnosis` 已注册到默认 Skill Registry；
- 第二步通过显式输出引用读取第一步的结构化结果；
- 第一步没有发现目标问题时，不创建任务；
- 第一步失败时，第二步保持阻断状态；
- 创建后保存外部 Task ID，并查询外部系统验证结果；
- Trace 能还原计划、输入绑定、执行结果和验证结果。

所需证据：

- 输出绑定单元测试；
- 日志 API 和任务 API 的集成测试；
- 一次真实或可重复沙箱环境的端到端演示；
- Workspace 中保存的日志证据和步骤输出。

### 场景 C：独立读取步骤并行执行

示例请求：

```text
查询项目文档，同时检查我明天下午的日历。
```

预期计划：

```text
step_1: knowledge_search
step_2: calendar_management.check_availability
step_1 与 step_2 无依赖，可并行执行
```

验收条件：

- Planner 不创建虚假依赖；
- TaskRunner 并行执行同一批可运行的读取步骤；
- 系统具有全局和 Skill 级最大并发限制；
- 单步超时或失败不会破坏另一个独立步骤的结果；
- 最终响应能够聚合两个步骤的结果；
- Trace 的起止时间可以证明步骤确实重叠执行。

所需证据：

- 两个固定延迟 Skill 的并行测试；
- 串行与并行耗时对比；
- 超时、取消和部分失败测试；
- 示例并行 Trace。

### 场景 D：需要确认的安全写操作

示例请求：

```text
帮我删除明天下午的项目评审会议。
```

预期执行：

```text
查询候选会议
→ 返回候选对象和 event_id
→ 请求用户确认
→ 用户明确确认
→ 执行删除
→ 查询 Calendar 验证删除结果
```

验收条件：

- 未得到明确确认时不执行删除；
- 确认内容与待删除对象、用户和会话绑定；
- 确认具有有效期，过期后必须重新确认；
- 重复提交同一确认不会重复执行；
- 写操作保存幂等键和外部 event ID；
- 网络超时后先验证外部状态，不盲目重复删除；
- Trace 记录确认、执行和验证三个独立阶段。

所需证据：

- 未确认、错误确认、过期确认和重复确认测试；
- Google Calendar Fake Backend 集成测试；
- 独立记录的 Live Integration；
- 删除前后外部状态证据。

### 场景 E：进程中断后的任务恢复

演示条件：任务执行到中间步骤时主动终止进程。

验收条件：

- 重启后能够从 Redis 读取未终结任务；
- 已经验证成功的步骤不会重复执行；
- 安全且未完成的步骤可以继续执行；
- 遗留 `RUNNING` 步骤不会永久卡住任务；
- 结果不明确的写操作进入 `UNKNOWN`；
- `UNKNOWN` 步骤先检查外部状态，再决定成功、失败或重试；
- 恢复过程本身写入 Trace；
- 最终状态与外部系统实际状态一致。

所需证据：

- 中断恢复集成测试；
- 写操作成功但响应超时的测试；
- 重启前后的 Redis 状态快照；
- 恢复过程 Trace 和外部状态验证结果。

## 5. 状态和数据约定

任务步骤至少支持以下状态：

```text
PENDING
READY
RUNNING
SUCCEEDED
FAILED
RETRYABLE
UNKNOWN
BLOCKED
CANCELLED
```

数据分层要求：

- `session_id`：最近对话和当前澄清上下文；
- `task_id`：计划、步骤、依赖、状态、幂等键和外部对象 ID；
- `user_id`：跨会话偏好，例如时区和默认日历；
- Redis：保存状态、短期上下文和引用；
- Workspace：保存原始材料、证据和大型步骤输出；
- Trace：保存执行事件和可观测性字段。

重要业务结果不得只存在于具有 TTL 的 Redis Key 中。

## 6. 可观测性验收

给定一个 `request_id`，系统必须能够还原：

```text
用户请求
→ 使用的上下文
→ Router/Planner 决策
→ Task DAG
→ 每一步输入和输出引用
→ Skill 执行状态
→ 外部对象 ID
→ 外部验证结果
→ 最终响应
```

Trace 至少记录：

- `request_id`、`session_id`、`task_id`、`step_id`；
- 模型和 Skill 名称；
- 开始时间、结束时间和持续时间；
- 路由和计划结果；
- Token 用量；
- 错误码、重试次数和恢复动作；
- 外部对象 ID；
- Workspace Evidence 引用；
- 外部状态验证结果。

任何性能指标必须同时记录：

- 测试数据集；
- 请求数量和并发度；
- 冷启动或预热状态；
- 模型和硬件配置；
- P50、P95 和失败率；
- 测试时间和对应 Git commit。

## 7. GitHub 可复现验收

首次使用者应能根据 README 完成：

```bash
git clone <repository>
cp .env.example .env
docker compose up --build
```

项目必须提供：

- 不需要真实凭据的 Mock/Demo 模式；
- 可选的 Slack、OpenAI、Google 和日志平台 Live 模式；
- 固定的示例数据和评测数据；
- 单元测试、集成测试、评测和 Demo 的独立命令；
- GitHub Actions；
- 架构、Demo、评测和安全配置文档；
- 不包含密钥、OAuth Token 或用户隐私数据的示例 Trace。

建议统一命令：

```text
make test
make test-integration
make eval
make demo
```

## 8. 最终交付清单

- [ ] Harness 是默认且唯一的正式执行主链路；
- [ ] Knowledge、Log Diagnosis、Calendar、Tasks 四类核心 Skill 已注册；
- [ ] Planner 支持合法 DAG 和步骤输出绑定；
- [ ] TaskRunner 支持受控并行、失败阻断和恢复；
- [ ] 写操作具有确认、幂等和外部状态验证；
- [ ] Redis、Workspace、User Profile 和 Trace 边界清晰；
- [ ] 三级 RAG 及其消融评测可复现；
- [ ] 五个最终 Demo 场景全部通过；
- [ ] 单元、集成、Live 和端到端证据分别记录；
- [ ] 本地 Demo 可以展示计划、步骤、证据和 Trace；
- [ ] Slack Demo 可以展示真实业务操作；
- [ ] Docker 和 README 支持从 GitHub 直接复现；
- [ ] 所有公开指标均可追溯到原始结果和 Git commit；
- [ ] 仓库中不存在密钥、Token 或真实用户隐私数据。

## 9. 当前不作为第一阶段完成条件的内容

以下能力可以在五个核心场景稳定后再扩展，不阻塞当前最终态验收：

- 多租户权限系统；
- Kubernetes 部署；
- 多区域容灾；
- 高并发生产 SLA；
- 自定义模型训练；
- 大规模在线学习；
- 复杂管理后台；
- 除四个核心 Skill 外的大量第三方集成。

这些能力不应提前引入，以免扩大当前项目范围。
