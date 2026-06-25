# PRCollect

统计 GitHub 用户已合并的 Pull Request，按仓库星数降序生成贡献表格。

## 功能

- 通过 GitHub GraphQL API 搜索指定用户所有已合并的公开 PR
- 按仓库 `stargazerCount` 降序排列，直观展示高影响力贡献
- 自动处理 API 速率限制，内置重试与等待机制
- 将生成的 Markdown 表格注入目标文件的标记区块中，方便集成到个人主页

## 环境要求

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

## 快速开始

### 1. 克隆项目

```bash
git clone <repo-url>
cd pr-collector-py
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 配置

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，填入你的 GitHub Token 和用户名：

```yaml
github:
  token: "ghp_xxxxxxxxxxxxxxxxxxxx"   # GitHub Personal Access Token

user:
  username: "your-github-username"     # 要统计的目标用户

output:
  target: "README.md"                  # 输出目标文件路径
```

> **Token 权限**：只需 `public_repo` 权限即可统计公开 PR。不提供 Token 将受到更严格的 API 速率限制（60次/小时 vs 5000次/小时）。
>
> **如何申请 Token**：
> 1. 访问 [GitHub Settings > Developer settings > Personal access tokens > Tokens (classic)](https://github.com/settings/tokens)
> 2. 点击 **"Generate new token (classic)"**
> 3. 填写 Note（如 `PRCollect`），勾选 `public_repo` 权限
> 4. 点击底部 **"Generate token"**，复制生成的 Token（注意仅显示一次）
> 5. 粘贴到 `config.yaml` 的 `token` 字段中

### 4. 在目标文件中放置标记

在目标 Markdown 文件中添加标记注释：

```markdown
<!-- Contributing block start -->

<!-- Contributing block end -->
```

### 5. 运行

```bash
uv run main.py
```

## 输出示例

运行后，标记区块会被自动填充为如下表格：

```markdown
<!-- Contributing block start -->
| # | 仓库 | Stars | PR |
|:---|:---|:---|:---|
| 1 | [muzimu/blog](https://github.com/muzimu/blog) | 1200 | [#42](https://github.com/muzimu/blog/pull/42) [#58](https://github.com/muzimu/blog/pull/58) |
| 2 | [foo/bar](https://github.com/foo/bar) | 856 | [#15](https://github.com/foo/bar/pull/15) |
<!-- Contributing block end -->
```

## 工作流程

1. 构造 GraphQL 搜索查询：`is:pr author:{username} archived:false is:closed is:merged is:public`
2. 分页拉取所有匹配的 PR 数据（每次最多 100 条）
3. 按仓库聚合，记录每个仓库的 PR 编号和星数
4. 按仓库星数降序排序后生成 Markdown 表格
5. 将表格注入目标文件的标记区块之间

## 速率限制处理

- 遇到 403/429 响应时，自动解析 `x-ratelimit-reset` 头计算等待时间
- 单次等待上限 60 秒，超时则终止程序
- 最多重试 10 次

## 项目结构

```
pr-collector-py/
├── main.py              # 主程序入口
├── pyproject.toml       # 项目元数据与依赖
├── config.example.yaml  # 配置文件模板
├── config.yaml          # 实际配置（已 gitignore）
├── test.md              # 示例目标文件
└── README.md
```

## 许可证

MIT
