from __future__ import annotations

import json
import sys
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: read-dsn-identity.py <dsn-file>")

    dsn = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    if not dsn:
        raise SystemExit("DSN file is empty")

    info = conninfo_to_dict(dsn)
    payload = {
        "dbname": info.get("dbname", "") or "",
        "user": info.get("user", "") or "",
    }
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
