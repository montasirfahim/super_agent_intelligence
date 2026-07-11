import sys
from urllib.request import urlopen

BASE_URL = "http://127.0.0.1:8000"


def main() -> int:
    try:
        with urlopen(f"{BASE_URL}/health", timeout=5) as response:
            body = response.read().decode()
            print("HEALTH_OK", body)
            return 0
    except Exception as exc:  # pragma: no cover - smoke test helper
        print("HEALTH_FAILED", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
