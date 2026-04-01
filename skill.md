---
name: notion_quadrant_manager
description: 基于 Notion 的四象限任务管理技能。通过 exec 调用同目录 Python 脚本完成 schema 识别、任务创建、查询、更新与总结。
metadata:
  openclaw:
    requires:
      bins:
        - python
        - python3
---

# Notion 四象限任务管理

## 1. 目标

基于用户配置的 Notion API 和数据库名称，自动完成以下能力：

- 读取数据库 schema，识别字段和枚举值
- 创建任务
- 查询今天未完成任务
- 查询最近 X 天的未完成任务
- 将任务标记为已完成
- 将任务标记为已取消
- 按四象限总结最近任务

## 2. 用户配置

用户必须提供：

- `notion_api_key`
- `notion_database_name`

如果数据库名称不存在、Notion 连接失败、或 schema 不包含必需字段，则必须提示用户修正配置。

## 3. 必要字段

Agent会自动识别数据库中与下列语义对应的字段：

- 待办事项
- 截止时间
- 优先级
- 状态
- 备注
- 分类

字段可以使用语义匹配，不要求完全同名，但必须存在对应的 Notion 属性类型和可用枚举值。

## 4. 调用方式

本技能通过 exec 调用同目录下的 Python 文件：

```bash
python3 notion_quadrant_manager.py <action> '<json_args>'
```

`json_args` 至少要包含：

- `notion_api_key`
- `database_name`

如果你所在环境只有 `python` 可用，也可以使用：

```bash
python notion_quadrant_manager.py <action> '<json_args>'
```

## 5. 可调用动作

### 5.1 bootstrap
先连接 Notion，定位数据库，读取 schema，并保存字段映射。

示例：

```bash
python3 notion_quadrant_manager.py bootstrap '{"notion_api_key":"xxx","database_name":"xxx"}'
```

### 5.2 add
创建任务。由Agent识别对话中的日期、事项、优先级、分类、备注（如有）、状态（默认未开始，如果用户表明正在做，则对应进行中），生成结构化数据输出给脚本执行。

示例：

```bash
python3 notion_quadrant_manager.py add '{"notion_api_key":"xxx","database_name":"xxx","title":"去北京","due_date":"2026-03-28","priority":"中","status":"未开始","category":"工作","note":"商务出差"}'
```

### 5.3 today
查询今天未完成的任务。

示例：

```bash
python3 notion_quadrant_manager.py today '{"notion_api_key":"xxx","database_name":"xxx"}'
```

### 5.4 recent
查询最近 X 天的未完成任务。

示例：

```bash
python3 notion_quadrant_manager.py recent '{"notion_api_key":"xxx","database_name":"xxx","days":7}'
```

### 5.5 complete
将当前任务标记为已完成。优先使用 `page_id`，否则使用最近一次任务上下文。

示例：

```bash
python3 notion_quadrant_manager.py complete '{"notion_api_key":"xxx","database_name":"xxx","page_id":"任务ID"}'
```

若用户只说“这个事情搞定了”，则调用时可不传 `page_id`，让脚本使用最近上下文。

### 5.6 cancel
将当前任务标记为已取消。优先使用 `page_id`，否则使用最近一次任务上下文。

示例：

```bash
python3 notion_quadrant_manager.py cancel '{"notion_api_key":"xxx","database_name":"xxx"}'
```

### 5.7 summary
总结最近任务，并按四象限统计。

示例：

```bash
python3 notion_quadrant_manager.py summary '{"notion_api_key":"xxx","database_name":"xxx","days":7}'
```

## 6. 口令映射

### 添加任务
用户输入类似：

- `3月28号去北京`
- `明天提交报告`

对应动作：

- `add`

### 查询今天任务
用户输入：

- `今天有什么事情没做`

对应动作：

- `today`

### 完成任务
用户输入：

- `这个事情搞定了`

对应动作：

- `complete`

### 取消任务
用户输入：

- `取消这个事情`

对应动作：

- `cancel`

### 总结最近任务
用户输入：

- `总结最近的任务`
- `最近有什么急事`

对应动作：

- `summary`

### 查询最近 X 天任务
用户输入：

- `最新3天有什么事没做`

对应动作：

- `recent`，并传入 `days=3`

## 7. 四象限判定

- **写入数据**：直接询问用户任务的四象限分类
- **总结分析**：根据数据库中的优先级和截止日期字段值判定四象限

按以下规则判断四象限：

- `重要紧急`：高优先级 + 截止时间距离今天不超过 2 天
- `重要不紧急`：高优先级 + 截止时间距离今天超过 2 天
- `紧急不重要`：低优先级 + 截止时间距离今天不超过 2 天
- `不重要不紧急`：低优先级 + 截止时间距离今天超过 2 天

## 8. 输出要求

Python 脚本返回 JSON，至少包含：

- `ok`
- `action`
- `message`
- `data`

Agent 读取 JSON 后再组织自然语言回复给用户。

## 9. 错误处理

必须明确提示以下情况：

- Notion API 连接失败
- 数据库名称找不到
- schema 识别失败
- 必要字段缺失
- 无法识别日期
- 无法定位要更新的任务