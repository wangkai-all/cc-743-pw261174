# 招生匹配分析应用（虚构数据）

一套零外部运行时依赖的招生匹配、补录政策比较与稳定性分析应用。所有学生、校区、成绩和名额均为虚构。

应用使用 Python 标准库实现确定性的学生提议延迟接受算法（Student-proposing DA），支持普通/专项池、跨校共享专项、专项席位回流、持久化并列抽签、承诺席位保护、退学补录链、容量/稳定性审计、逐志愿解释和小实例穷举核验。

## 启动

```bash
cd /workspace
python3 -m admissions.server
# 打开 http://127.0.0.1:8000
```

可通过环境变量修改端口或状态文件：

```bash
PORT=8010 ADMISSIONS_STATE_PATH=/tmp/admissions-state.json python3 -m admissions.server
```

首次启动会自动生成 `/workspace/var/state.json`，其中包含主演示数据和第 1 轮已保存结果。删除该文件或点击页面上的“重置主演示”可恢复初始状态。

## 测试与穷举核验

```bash
cd /workspace
python3 -m unittest discover -s tests -v
python3 -m admissions.verify
```

当前测试覆盖：

- DA 容量、志愿归属和独立稳定性；
- 抽签号持久化与重复计算确定性；
- 成绩硬门槛和拒绝事件；
- 同层级并列；
- 跨校 `J1` 共享容量与宿主校区容量；
- 未用专项席位回流对结果的改变；
- 退学后的席位释放和状态衔接；
- “整体重算”与“保留承诺”的差异；
- 承诺导致的 blocking pair 与三人交换联盟；
- 容量低于承诺时的显式冲突；
- 小实例全组合穷举、stable matching 数量和 student-optimal 性质；
- HTTP API 与静态首页冒烟。

## 页面能力

- **录取与“为什么”**：学生分配总表；逐志愿说明容量、优先层级、保存抽签号、末位录取者和拒绝原因。
- **提交 / 容量 / 撤回**：
  - 新增学生并提交最多四个志愿；
  - 修改一个校区普通/专项容量或跨校专项总容量；
  - 撤回学生并保存原因；
  - 同一操作并行预览“整体重算”和“保留承诺”，确认后才写入新一轮；
  - “无变更比较”只并行计算当前状态下的两种政策，不创建轮次，也不显示提交按钮。
- **容量与回流**：普通/专项容量 meter、审计表、专项未用余额、是否回流、回流目标和宿主安置。
- **稳定性与联盟**：blocking pairs、承诺归因、长度 2–4 的短周期交换联盟、容量/资格/承诺冲突。
- **每轮申请日志**：保存每轮每个事件的申请、暂留、拒绝、被挤出、宿主安置和中文原因。
- **穷举核验**：页面可直接运行 24 组随机小实例加 4 个命名场景。
- **种子与并列**：展示保存种子、每个项目/学生的抽签号和严格排序键。

## 核心规则

### 1. 池别与项目

- 学生属于 `normal` 或 `special` 池。
- 普通校区（`programs`）分别配置 `quotas.normal` 和 `quotas.special`。
- 跨校共享专项（`joint_programs`）有：
  - 全项目共享的 `capacity`；
  - 多个宿主校区及每个宿主的安置容量；
  - 独立的优先顺序和成绩门槛；
  - 按联合专项优先级进行宿主校区串行安置。
- 普通池学生不能占用跨校专项；专项池学生先参加专项阶段，再按规则处理未用席位回流。

### 2. 优先级与持久抽签

每个项目有层级顺序，例如：

```python
["sibling", "legacy", "diversity"]
```

对应：

- `sibling`：兄弟姐妹在读；
- `legacy`：教职工子女；
- `diversity`：就近/多元；
- `none`：无优先。

严格排序键为：

```text
(priority_rank, persisted_lottery_draw, candidate_id)
```

抽签号由保存的种子、项目 ID 和学生 ID 通过 SHA-256 派生：

```python
lottery_draw(seed_text, accepter_id, candidate_id)
```

首次需要时写入 `state.tie_vectors`，之后已有值永不覆盖。因此重复计算、重启服务都不会重新洗牌。

### 3. DA 与专项回流

计算分两阶段：

1. 专项池学生竞争校区专项名额和跨校共享专项；
2. 专项阶段结束后计算未用席位：
   - `convert_special_to_normal: true` 时，校区未用专项名额加入本校普通容量；
   - joint 配置 `convert_unused` 和 `convert_to.program_id` 时，未用共享专项名额加入指定普通校区；
   - 配置为不转换时，空位作为专项保留席位展示；
   - 应回流但缺少目标时写入 `conversion_without_target` 冲突。

回流发生在普通池 DA 之前，因此会真实改变普通池竞争，而不是事后会计调整。

### 4. 两种政策

- `full_recompute`：忽略旧承诺，在当前容量、志愿、资格和退学状态上从头运行 DA。
- `protect_promises`：把此前已接受且未释放的席位作为不可驱逐承诺，先从可竞争容量中扣除；新 DA 只竞争剩余席位。

每轮提交后，最终分配会成为下一轮的新承诺集合；旧 seat 记录标记 `released_in_round`，保留完整历史。

### 5. 补录链

`admissions/chains.py` 比较前后两轮 `candidate_id -> accepter_id`：

- 退学学生的旧席位是空位根；
- 容量增加的项目是新席位根；
- 找到补入该项目的学生后，继续追溯其上一轮项目；
- 直到一名此前未录取的学生补入，或席位无人补上。

主演示数据中的 **C02 退学** 是推荐查看的分歧场景：

- 整体重算：`C01: P3 → P1`，随后 `C18: 未录取 → P3`，形成两步链；
- 保留承诺：C01 的 P3 席位受保护、不能移动，P1 由 C08 直接补入；审计会发现 `C01 → P1` 是必须打破 C01 承诺才能消除的 blocking pair。

### 6. 稳定性与冲突语义

独立审计重新检查每个学生当前录取志愿之前的所有志愿：

- 项目有空位：`kind = "vacancy"`；
- 学生严格优于当前末位 holder：`kind = "priority"`；
- 若消除该组合必须打破承诺，标记 `promise_caused = true`。

同时构造有向边 `candidate -> worst_holder`，检测长度 2–4 的环作为短周期 blocking coalition。

结果区分：

- `stability.stable`：不存在任何 blocking pair/coalition，也没有阻断性冲突；
- `stability.constrained_stable`：不存在非承诺导致的结构性阻挡，但可能仍有承诺导致的组合。

当承诺数超过容量、承诺学生不再满足门槛、宿主校区超额或联合专项无法安置时，结果状态为 `conflict`。应用不会把容量不足或被迫不稳定的方案显示为“全部稳定”。

## 穷举验证的范围

`admissions/verify.py` 对小实例枚举：

```text
每个学生 → None 或其某个志愿
```

的全部组合，独立检查容量并寻找 blocking pairs，然后验证：

1. 引擎输出满足容量和志愿约束；
2. 引擎输出没有 blocking pair；
3. 对每个枚举到的 stable matching，DA 结果对每名学生都至少同样偏好；
4. 重复运行结果和抽签向量不变；
5. 跨校共享容量、回流和退学衔接命名场景通过。

穷举只用于足够小的实例；主演示数据使用 `audit.py` 的独立容量/稳定性核对。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/state` | 当前状态、轮次元数据和当前结果 |
| GET | `/api/rounds` | 轮次列表 |
| GET | `/api/rounds/<n>` | 某轮完整结果、事件和快照 |
| GET | `/api/explain?candidate_id=C01&round=1` | 逐志愿中文解释 |
| POST | `/api/preview` | 预览操作；可传 `policy`，不传则同时返回两种政策 |
| POST | `/api/commit` | 保存操作、计算结果、事件和新一轮承诺 |
| POST | `/api/reset` | 重置为 `demo` 或 `coalition` 场景 |
| GET/POST | `/api/verify`、`/api/verify/run` | 穷举核验 |

预览请求示例：

```bash
curl -s -X POST http://127.0.0.1:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{
    "action": {
      "type": "withdraw",
      "candidate_id": "C02",
      "reason": "家庭搬迁"
    }
  }'
```

## 内置场景

### 主演示：清河学区

- P1–P4 四个虚构校区；
- J1 跨校共享科创专项，宿主为 P2/P4；
- C01/C02 在 P1 同属 sibling 层，由保存抽签决定；
- C03/C04 在 P2 同属 legacy 层；
- C06 数学 58，未过 P1 的 60 分门槛；
- P1/P2 未用专项回流普通池，P4 配置为保留专项空位；
- 18 名学生中初始 15 人录取、3 人未录取，存在拒绝解释和竞争传导。

页面表单中的“填入‘承诺阻挡’示例”会新增 P2 sibling 学生。整体重算会录取该生并替换末位 diversity holder；保留承诺时该生无法进入 P2，审计输出 promise-caused blocking pair。

### 承诺联盟示例

点击“载入承诺联盟示例”加载 K1–K3、A1–A3 的三人三校构造：

- 历史承诺为 `K1→A1`、`K2→A2`、`K3→A3`；
- 优先关系分别使 K1 更适合 A2、K2 更适合 A3、K3 更适合 A1；
- 保留承诺时存在三人 blocking coalition；
- 整体重算得到 `K1→A2`、`K2→A3`、`K3→A1`，恢复完全稳定。

## 代码结构

```text
admissions/
  model.py       # JSON state、优先级键、抽签、资格、校验
  engine.py      # 两阶段 DA、回流、承诺、joint host placement
  audit.py       # 独立容量审计、blocking pairs、周期联盟
  chains.py      # 退学/增容补录链
  explain.py     # 逐志愿解释和事件中文原因
  verify.py      # 小实例穷举验证
  fixtures.py    # 主演示和承诺联盟构造数据
  store.py       # 原子 JSON 持久化、preview/commit 服务
  server.py      # 标准库 HTTP API 和静态文件
static/
  index.html
  styles.css
  app.js
tests/
  test_engine.py
  test_service_web.py
var/
  state.json     # 运行时生成，已 .gitignore
```

## 已知边界

- 联合专项的宿主安置采用按 joint 优先级排序的串行选择；它保证 joint 容量和宿主容量，但宿主匹配本身不另行运行双向 DA。
- Blocking coalition 检测聚焦长度 2–4 的有向交换环，不声称枚举了所有长度的联盟。
- “保留承诺”把旧席位作为不可驱逐底线；因此它可能不是完全稳定匹配，页面会明确列出承诺导致的阻挡组合。
- 前端为无构建步骤的原生 HTML/CSS/JavaScript，不依赖 CDN。
