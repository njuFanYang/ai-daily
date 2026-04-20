# AI Daily

每日自动抓取 AI/LLM 领域权威源，由 Claude 精选 Top 10 + 中文摘要 + 生成静态网页。

## 架构

```
crawl 18+ sources → score (4-dim weighted) → Claude pick top 10
                                              ↓
                       Claude 中文摘要 (一篇一调) → Jinja2 渲染 HTML
                                              ↓
                       静态站点 web/index.html + archive/<date>.html

每 14 天：Claude 回看过去精选 → 自我修正 weights.yaml
```

## 目录

```
ai-daily/
├── server/              # Python 流水线
│   ├── sources.yaml     # 资讯源列表 (RSS / API / HTML)
│   ├── weights.yaml     # 4 维权重 + 主题表 (reflection 自动更新)
│   ├── crawler.py       # 拉取所有源，归一化为统一 schema
│   ├── score.py         # 4 维加权打分 → top 60
│   ├── claude_pick.py   # Claude 从 top 60 精选 top 10
│   ├── claude_summarize.py  # 每篇生成中文摘要
│   ├── claude_reflect.py    # 双周反思，调权重
│   ├── render.py        # Jinja2 渲染静态 HTML
│   ├── pipeline.py      # 串联整套流水线
│   └── data/
│       ├── raw/<date>.json       # 当日全部抓取
│       ├── scored/<date>.json    # 打分排序后 top 60
│       ├── picked/<date>.json    # Claude 精选 + 中文摘要
│       ├── reflections/          # 反思报告
│       └── weights_history/      # 历史权重版本
├── web/                 # 客户端：纯静态站点
│   ├── index.html       # 当日精选 (= 最新一天)
│   ├── archive/         # 历史归档
│   └── assets/style.css
└── scheduler/           # Windows 任务计划脚本
    ├── run_daily.bat
    ├── run_reflect.bat
    └── setup_task.ps1
```

## 快速开始

```bash
cd E:\Develop\ai-daily\server
pip install -r requirements.txt

# 手动跑一次完整流水线
python pipeline.py

# 浏览结果（双击或简单 server）
start ..\web\index.html
# 或：python -m http.server -d ..\web 8080  → http://localhost:8080
```

## 注册定时任务（Windows 任务计划）

以**管理员身份**打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File E:\Develop\ai-daily\scheduler\setup_task.ps1
```

会注册：
- `AIDaily-Pipeline` — 每天 08:00 跑完整流水线
- `AIDaily-Reflect`  — 每两周日 09:00 跑权重反思

## 权重设计

`weights.yaml` 控制打分逻辑，4 维加权：

| 维度 | 占比 | 来源 |
|---|---|---|
| source_authority | 35% | 官方实验室=1.0，HN=0.7，Reddit=0.6 |
| topic_match | 30% | Claude 标签 × topic 权重表 |
| engagement | 20% | HN points / HF trending rank / Reddit ups |
| recency | 15% | 24h 内满分，72h 半衰，7 天截止 |

每 14 天 `claude_reflect.py` 自动反思并调整：
- 哪些主题应该提/降权？
- 哪些源持续高质？
- 是否漏掉了重要事件？
- 4 维占比是否需要重新平衡？

输出新 `weights.yaml` + 反思报告（保存在 `data/reflections/`），旧版本归档到 `data/weights_history/` 可随时回滚。

## 添加新源

编辑 `server/sources.yaml`：

```yaml
  - name: my_new_source
    type: rss              # rss / arxiv_rss / hn_algolia / reddit_json / hf_papers / github_releases / anthropic_html
    url: https://...
    category: lab_official
    enabled: true          # 默认 true，可临时禁用
```

同时在 `weights.yaml` 的 `source_weights` 加上初始权重（建议 0.5-0.7 起步，让反思机制慢慢调整）。

## 调试

- 单步执行：`python crawler.py` / `python score.py` / `python claude_pick.py` / `python claude_summarize.py` / `python render.py`
- 重跑指定日期：`python pipeline.py --date 2026-04-20`
- 跳过爬取（用现有 raw 数据）：`python pipeline.py --skip-crawl`
- 反思 dry-run：`python claude_reflect.py --dry-run`
- 日志：`server/logs/{crawler,score,pick,summarize,render,reflect,pipeline,cron}.log`

## 已知问题

下列源因为反爬/接口变动暂时禁用（在 sources.yaml 里 `enabled: false`）：
- `import_ai`、`the_batch`、`meta_ai`、`mistral_news`、`reddit_machinelearning`、`reddit_claudeai`、`reddit_openai`

需要时可改写对应 fetcher（如增加 OAuth、换镜像、改 HTML 抓取）。
