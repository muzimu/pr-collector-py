import time
from collections import defaultdict
from pathlib import Path

import requests
import yaml

CONFIG_PATH = Path(__file__).parent / 'config.yaml'


def load_config():
    """从 YAML 文件加载配置。"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {CONFIG_PATH}\n"
            f"请复制 config.example.yaml 为 config.yaml 并填入 token"
        )
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


config = load_config()
TOKEN = config['github']['token']
USERNAME = config['user']['username']
TARGET = config['output']['target']

# GitHub API 要求设置 User-Agent，否则请求会被拒绝
# 未认证：60 次/小时，认证后：5000 次/小时
HEADERS = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'token {TOKEN}',
    'User-Agent': 'prcollect/0.1.0',
    'X-GitHub-Api-Version': '2022-11-28',
}

# 速率限制相关常量
RATE_LIMIT_MAX_WAIT_SECONDS = 60      # 最多等待 60 秒，超过则直接退出
RATE_LIMIT_MAX_RETRIES = 10           # 安全上限，防止极端情况下的无限循环


def _parse_rate_limit_headers(resp):
    """从响应头解析 GitHub 速率限制信息。

    返回包含以下字段的 dict，解析失败返回 None：
      - limit:     每小时最大请求数 (x-ratelimit-limit)
      - remaining: 当前窗口剩余次数 (x-ratelimit-remaining)
      - reset:     限额重置的 UTC 时间戳 (x-ratelimit-reset)
      - used:      已使用的请求数 (x-ratelimit-used)
      - resource:  请求计数的资源类型 (x-ratelimit-resource)
    """
    try:
        return {
            'limit': int(resp.headers.get('x-ratelimit-limit', '0')),
            'remaining': int(resp.headers.get('x-ratelimit-remaining', '0')),
            'reset': int(resp.headers.get('x-ratelimit-reset', '0')),
            'used': int(resp.headers.get('x-ratelimit-used', '0')),
            'resource': resp.headers.get('x-ratelimit-resource', 'unknown'),
        }
    except (ValueError, TypeError):
        return None


def _request_with_retry(url, error_prefix=""):
    """带速率限制智能重试的 HTTP GET 请求，返回 (status_code, json_data 或 None)。

    当触发速率限制（403/429）时：
      1. 从响应头读取 x-ratelimit-reset 计算剩余等待时间
      2. 若等待时间 <= 60s：等待后重试
      3. 若等待时间 > 60s：直接停止程序
    """
    for attempt in range(RATE_LIMIT_MAX_RETRIES):
        resp = requests.get(url, headers=HEADERS)
        status = resp.status_code

        if status == 200:
            return status, resp.json()

        if status == 403 or status == 429:
            rate_info = _parse_rate_limit_headers(resp)

            if rate_info:
                reset_ts = rate_info['reset']
                now_ts = int(time.time())
                wait_seconds = max(reset_ts - now_ts, 0)

                print(
                    f"[速率限制] {error_prefix} 请求被限流（{status}），"
                    f"资源: {rate_info['resource']}，"
                    f"已用: {rate_info['used']}/{rate_info['limit']}，"
                    f"重置时间戳: {reset_ts}，需等待: {wait_seconds}s"
                )

                if wait_seconds <= RATE_LIMIT_MAX_WAIT_SECONDS:
                    # 等待时间在可接受范围内，等待后重试（+1s 缓冲避免边界竞争）
                    print(f"[等待] 等待 {wait_seconds + 1}s 后重试（{attempt + 1}/{RATE_LIMIT_MAX_RETRIES}）...")
                    time.sleep(wait_seconds + 1)
                    continue
                else:
                    # 等待时间过长，直接终止
                    print(
                        f"[终止] {error_prefix} 速率限制需等待 {wait_seconds}s，"
                        f"超过最大等待 {RATE_LIMIT_MAX_WAIT_SECONDS}s，停止程序。"
                    )
                    raise SystemExit(1)
            else:
                # 无速率限制头（非标准 403，可能是权限问题）
                print(f"[终止] {error_prefix} HTTP {status}，无法解析速率限制头，停止程序。URL: {url}")
                raise SystemExit(1)

        # 其他错误（404 等）
        print(f"[错误] {error_prefix} HTTP {status}，URL: {url}")
        return status, None

    # 安全上限触发（极端情况）
    print(f"[终止] {error_prefix} 速率限制重试达到安全上限（{RATE_LIMIT_MAX_RETRIES} 次），停止程序。")
    raise SystemExit(1)


def generate_contributing_table(username, target):
    search_url = (
        f'https://api.github.com/search/issues?per_page=100'
        f'&q=is%3Apr+author%3A{username}+archived%3Afalse+is%3Aclosed+is%3Amerged+is%3Apublic+type%3Apr&page='
    )
    data = []
    page = 1
    failed_pages = []

    # 分页获取所有 PR
    while True:
        status, body = _request_with_retry(search_url + str(page), error_prefix=f"搜索 PR (page={page})")
        if body is None:
            failed_pages.append(page)
            if page == 1:
                raise RuntimeError(f"搜索 API 首页请求失败，无法继续")
            break

        total = body['total_count']
        data.extend(body['items'])
        if total <= page * 100:
            break
        page += 1

    if failed_pages:
        print(f"[警告] 以下页面获取失败，数据可能不完整: {failed_pages}")

    repos = defaultdict(list)
    for item in data:
        repo = item['repository_url'].removeprefix('https://api.github.com/repos/')
        number = item['number']
        repos[repo].append(number)

    # 获取每个仓库的 star 数
    repo_stars = {}
    failed_repos = []
    for repo in repos:
        status, body = _request_with_retry(
            f'https://api.github.com/repos/{repo}',
            error_prefix=f"仓库 {repo}"
        )
        if body is not None and status == 200:
            repo_stars[repo] = body.get('stargazers_count', 0)
        else:
            # API 调用失败，标记为 -1 以区分"真实 0 星"
            repo_stars[repo] = -1
            failed_repos.append(repo)

    if failed_repos:
        print(f"[警告] 以下仓库的 star 数获取失败（显示为 ?）: {len(failed_repos)} 个")
        for r in failed_repos:
            print(f"  - {r}")

    items = sorted(repos.items(), key=lambda x: repo_stars.get(x[0], -1), reverse=True)

    # Generate markdown table
    table_lines = [
        "| # | 仓库 | Stars | PR |",
        "|:---|:---|:---|:---|"
    ]

    for index, (repo, numbers) in enumerate(items, 1):
        stars = repo_stars.get(repo, -1)
        stars_display = "?" if stars == -1 else str(stars)
        pr_links = [f"[#{n}](https://github.com/{repo}/pull/{n})" for n in numbers]
        pr_list = " ".join(pr_links)
        table_lines.append(f"| {index} | [{repo}](https://github.com/{repo}) | {stars_display} | {pr_list} |")

    # Read the target file
    with open(target, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace the table block
    start_marker = "<!-- Contributing block start -->\n"
    end_marker = "\n<!-- Contributing block end -->"
    table_content = "\n".join(table_lines)
    start_index = content.find(start_marker)
    end_index = content.find(end_marker)
    assert start_index != -1 and end_index != -1, "Contributing block not found"
    new_content = content[:start_index+len(start_marker)] + table_content + content[end_index:]

    # Write back to the file
    with open(target, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("done")

if __name__ == "__main__":
    generate_contributing_table(USERNAME, TARGET)
