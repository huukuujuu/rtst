from __future__ import annotations

import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()

    try:
        from rtst_app.app import run_app
    except ModuleNotFoundError as exc:
        missing = exc.name or "a dependency"
        print(f"Missing dependency: {missing}")
        print("Run: pip install -r requirements.txt")
        return 1

    return run_app(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
