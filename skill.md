---
name: notion_quadrant_manager
description: 基于 Notion 的四象限任务(待办事项)管理技能。通过 exec 调用 Python 脚本完成数据库连接、任务创建、查询、更新与总结。
metadata:
  openclaw:
    requires:
      bins:
        - python
        - python3
    version: "2.0.0"
    author: "OpenClaw Team"
    license: "MIT"
---

# Notion 四象限任务管理

## 1. 触发条件

当用户输入包含以下意图时，触发本技能：

- **添加任务**：用户提到添加、创建、新建、任务、待办、要做等关键词
- **查询今天任务**：用户提到今天、现在、当前、待办、任务等关键词
- **查询最近任务**：用户提到最近、过去、前、天、周、未完成等关键词且包含时间范围
- **完成任务**：用户提到完成、搞定、做完、结束等关键词
- **取消任务**：用户提到取消、删除、不要、中止等关键词
- **总结任务**：用户提到总结、分析、统计、最近、四象限等关键词

## 2. 用户配置

### 2.1 API 密钥配置
用户需要按照以下步骤配置 API 密钥：
1. 在 https://notion.so/my-integrations 创建集成
2. 复制 API 密钥（以 ntn_ 或 secret_ 开头）
3. 执行以下命令存储 API 密钥：
   ```bash
   mkdir -p ~/.config/notion
   echo "your_api_key_here" > ~/.config/notion/api_key
   ```
4. 确保将目标页面/数据库分享给你的集成（点击 "..." → "Connect to" → 你的集成名称）

### 2.2 数据库配置
用户必须提供：
- `notion_database_name`：数据库名称

如果数据库名称不存在、Notion 连接失败、或缺少必需字段，则提示用户修正配置。

## 3. 必要字段

Agent会自动识别数据库中与下列语义对应的字段：
- 待办事项（标题字段）
- 截止时间（日期字段）
- 四象限（select/status/multi_select 字段）
- 状态（status/select 字段）
- 备注（rich_text/title 字段）
- 分类（multi_select/select 字段）

字段可以使用语义匹配，不要求完全同名，但必须存在对应的 Notion 属性类型和可用枚举值。

## 4. 调用方式

本技能通过 exec 调用同目录下的 Python 文件：

```bash
python3 notion_quadrant_manager.py <action> '<json_args>'
```

`json_args` 必须包含：
- `database_name`：数据库名称

API 密钥会自动从 `~/.config/notion/api_key` 文件读取。

## 5. 可调用动作

### 5.1 bootstrap
连接 Notion，定位数据库，读取 schema，并保存字段映射。

**参数**：
- `notion_api_key`：Notion API 密钥
- `database_name`：数据库名称

**返回**：
- 数据库连接信息
- 字段映射

**示例**：
```bash
python3 notion_quadrant_manager.py bootstrap '{"database_name":"xxx"}'
```

### 5.2 add
创建任务。由 Agent 识别对话中的日期、事项、四象限、分类、备注、状态，生成结构化数据输出给脚本执行。

**参数**：
- `notion_api_key`：Notion API 密钥
- `database_name`：数据库名称
- `title`：任务标题
- `due_date`：截止日期（ISO 格式）
- `quadrant`：四象限分类
- `status`：状态（默认：未开始）
- `category`：分类（可选）
- `note`：备注（可选）

**返回**：
- 创建的任务信息

**示例**：
```bash
python3 notion_quadrant_manager.py add '{"database_name":"xxx","title":"去北京","due_date":"2026-03-28","quadrant":"重要紧急","status":"未开始","category":"工作","note":"商务出差"}'
```

### 5.3 today
查询今天未完成的任务。

**参数**：
- `notion_api_key`：Notion API 密钥
- `database_name`：数据库名称

**返回**：
- 今天未完成的任务列表（包含超时任务提醒）

**示例**：
```bash
python3 notion_quadrant_manager.py today '{"database_name":"xxx"}'
```

### 5.4 recent
查询最近 X 天的未完成任务。

**参数**：
- `notion_api_key`：Notion API 密钥
- `database_name`：数据库名称
- `days`：天数

**返回**：
- 最近 X 天未完成的任务列表（包含超时任务提醒）

**示例**：
```bash
python3 notion_quadrant_manager.py recent '{"database_name":"xxx","days":7}'
```

### 5.5 complete
将任务标记为已完成。优先使用 `page_id`，否则使用最近一次任务上下文。

**参数**：
- `notion_api_key`：Notion API 密钥
- `database_name`：数据库名称
- `page_id`：任务 ID（可选）
- `text`：任务描述（用于查找任务，可选）

**返回**：
- 更新后的任务信息

**示例**：
```bash
python3 notion_quadrant_manager.py complete '{"database_name":"xxx","page_id":"任务ID"}'
```

### 5.6 cancel
将任务标记为已取消。优先使用 `page_id`，否则使用最近一次任务上下文。

**参数**：
- `notion_api_key`：Notion API 密钥
- `database_name`：数据库名称
- `page_id`：任务 ID（可选）
- `text`：任务描述（用于查找任务，可选）

**返回**：
- 更新后的任务信息

**示例**：
```bash
python3 notion_quadrant_manager.py cancel '{"database_name":"xxx"}'
```

### 5.7 summary
总结最近任务，并按四象限统计。

**参数**：
- `notion_api_key`：Notion API 密钥
- `database_name`：数据库名称
- `days`：天数（默认：7）

**返回**：
- 四象限统计
- 重要紧急任务列表
- 超时任务列表
- 建议

**示例**：
```bash
python3 notion_quadrant_manager.py summary '{"database_name":"xxx","days":7}'
```

## 6. 四象限处理

### 6.1 写入数据流程
1. 从用户输入中提取任务内容、日期等信息
2. 如果用户直接告知了四象限，无需再询问
3. 如果用户未指定四象限，使用标准化模板询问用户四象限分类
4. 等待用户选择四象限
5. 将结构化数据传递给脚本执行

### 6.2 四象限询问模板
"这个任务属于哪个四象限分类？请选择：
1. 重要紧急（需要立即处理）
2. 重要不紧急（需要计划安排）
3. 紧急不重要（可以委托他人）
4. 不重要不紧急（可以考虑取消）"

### 6.3 总结分析流程
1. 检查数据库中是否有四象限字段，如有则直接使用该值判定
2. 识别日期早于当前时间的任务，单独列出并重点提醒已超时
3. 根据数据库中的四象限字段值判定
4. 生成四象限统计和建议

### 6.4 四象限判定规则
- `重要紧急`：优先级 1
- `紧急不重要`：优先级 2
- `重要不紧急`：优先级 3
- `不重要不紧急`：优先级 4

四象限同时作为优先级使用，排序为：重要紧急 > 紧急不重要 > 重要不紧急 > 不重要不紧急

## 7. 超时任务处理

- 识别截止时间早于当前时间的任务
- 在查询和总结时单独列出这些任务
- 使用醒目的方式提醒用户这些任务已超时
- 超时任务优先显示在任务列表顶部

## 8. 输出要求

Python 脚本返回 JSON，至少包含：
- `ok`：操作是否成功
- `action`：执行的动作
- `message`：操作结果消息
- `data`：操作结果数据

Agent 读取 JSON 后再组织自然语言回复给用户。

## 9. 错误处理

Agent 应处理以下错误：
- **API 连接失败**：提示用户检查 API 密钥和网络连接
- **数据库找不到**：提示用户检查数据库名称是否正确
- **Schema 识别失败**：提示用户检查数据库结构是否符合要求
- **字段缺失**：提示用户添加必要的字段
- **日期识别失败**：提示用户使用标准日期格式
- **任务定位失败**：提示用户提供更具体的任务信息
- **网络超时**：提示用户检查网络连接并重试
- **API 限流**：提示用户稍后重试
- **权限不足**：提示用户检查 API 密钥权限
