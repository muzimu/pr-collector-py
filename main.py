import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

CONFIG_PATH = Path(__file__).parent / 'config.yaml'


def load_config():
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

GRAPHQL_URL = 'https://api.github.com/graphql'
HEADERS = {
    'Authorization': f'bearer {TOKEN}',
    'User-Agent': 'prcollect/0.1.0',
    'Content-Type': 'application/json',
}

SEARCH_PR_QUERY = '''
query($queryString: String!, $cursor: String) {
  search(query: $queryString, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        ... on PullRequest {
          number
          repository {
            nameWithOwner
            stargazerCount
          }
        }
      }
    }
  }
  rateLimit {
    remaining
    resetAt
  }
}
'''

MAX_WAIT_SECONDS = 60
MAX_RETRIES = 10


def _graphql_request(query, variables):
    payload = {'query': query, 'variables': variables}

    for attempt in range(MAX_RETRIES):
        resp = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload)
        resp_json = resp.json()

        if resp.status_code == 200:
            if not resp_json.get('errors'):
                return resp_json['data']

            error_msg = '; '.join(e.get('message', '') for e in resp_json['errors'])
            if 'rate limit' in error_msg.lower():
                rate = resp_json.get('data', {}).get('rateLimit', {})
                reset_at = rate.get('resetAt', '')
                try:
                    reset_dt = datetime.fromisoformat(reset_at.replace('Z', '+00:00'))
                    wait_seconds = max((reset_dt - datetime.now(timezone.utc)).total_seconds(), 0)
                except (ValueError, TypeError):
                    wait_seconds = MAX_WAIT_SECONDS + 1
            else:
                wait_seconds = 60
        elif resp.status_code in (403, 429):
            reset_ts = int(resp.headers.get('x-ratelimit-reset', '0'))
            wait_seconds = max(reset_ts - int(time.time()), 0)
        else:
            print(f"[错误] HTTP {resp.status_code}")
            return None

        print(f"[速率限制] 需等待 {wait_seconds:.0f}s")

        if wait_seconds > MAX_WAIT_SECONDS:
            raise SystemExit(f"等待时间 {wait_seconds:.0f}s 超过上限 {MAX_WAIT_SECONDS}s，停止程序。")

        print(f"[等待] {wait_seconds + 1:.0f}s 后重试 ({attempt + 1}/{MAX_RETRIES})...")
        time.sleep(wait_seconds + 1)

    raise SystemExit(f"重试达到上限 ({MAX_RETRIES} 次)，停止程序。")


def generate_contributing_table(username, target):
    search_query = (
        f'is:pr author:{username} archived:false is:closed is:merged is:public'
    )

    repos = defaultdict(lambda: {'numbers': [], 'stars': 0})
    cursor = None

    while True:
        data = _graphql_request(
            SEARCH_PR_QUERY,
            {'queryString': search_query, 'cursor': cursor},
        )

        if data is None:
            raise RuntimeError("GraphQL 请求失败")

        for edge in data['search']['edges']:
            node = edge.get('node')
            if not node:
                continue
            repo = node['repository']
            name = repo['nameWithOwner']
            repos[name]['numbers'].append(node['number'])
            repos[name]['stars'] = repo['stargazerCount']

        if not data['search']['pageInfo']['hasNextPage']:
            break
        cursor = data['search']['pageInfo']['endCursor']

    items = sorted(repos.items(), key=lambda x: x[1]['stars'], reverse=True)

    table_lines = [
        "| # | 仓库 | Stars | PR |",
        "|:---|:---|:---|:---|"
    ]

    for index, (repo_name, info) in enumerate(items, 1):
        numbers = sorted(set(info['numbers']))
        pr_links = [f"[#{n}](https://github.com/{repo_name}/pull/{n})" for n in numbers]
        table_lines.append(
            f"| {index} | [{repo_name}](https://github.com/{repo_name}) | {info['stars']} | {' '.join(pr_links)} |"
        )

    with open(target, 'r', encoding='utf-8') as f:
        content = f.read()

    start_marker = "<!-- Contributing block start -->\n"
    end_marker = "\n<!-- Contributing block end -->"
    table_content = "\n".join(table_lines)
    start_index = content.find(start_marker)
    end_index = content.find(end_marker)
    assert start_index != -1 and end_index != -1, "Contributing block not found"
    new_content = content[:start_index + len(start_marker)] + table_content + content[end_index:]

    with open(target, 'w', encoding='utf-8') as f:
        f.write(new_content)

    total_prs = sum(len(info['numbers']) for info in repos.values())
    print(f"{total_prs} 个 PR，{len(repos)} 个仓库 → {target}")


if __name__ == "__main__":
    generate_contributing_table(USERNAME, TARGET)
