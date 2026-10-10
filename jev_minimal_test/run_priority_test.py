"""Run a minimal Jev score decision using the repository .env file."""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


API_URL = "https://jevmodel.org/v1/systemone"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Load the API key, ask Jev to score urgency, and print the response."""
    load_dotenv(REPOSITORY_ROOT / ".env")
    api_key = os.environ.get("JEVMODEL_API_KEY")
    if not api_key:
        print(
            "Missing JEVMODEL_API_KEY in the repository .env file.",
            file=sys.stderr,
        )
        return 2

    payload = {
        "model": "jev-latest",
        "state": "公司的登录服务完全不可用，所有客户都无法登录，业务已经中断。",
        "questions": {
            "urgency": {
                "type": "score",
                "instructions": "这个问题的紧急程度如何？",
                "criteria": ["低", "中", "高", "严重"],
            }
        },
    }
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        print(f"Jev returned HTTP {error.code}: {error_body}", file=sys.stderr)
        return 1
    except URLError as error:
        print(f"Could not reach Jev: {error.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print("The Jev request timed out.", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
