"""python -m b210_sar.gui [--host H] [--port N] [--source none] [--config-root DIR]"""

from __future__ import annotations

import argparse
import os

from .sources import source_names


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="b210_sar.gui", description="B210 SAR GUI server")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0; the GUI has no authentication)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CONSOLE_B210_GUI_PORT", "8010")),
                    help="default 8010, or $CONSOLE_B210_GUI_PORT")
    ap.add_argument("--source", choices=source_names(), default="none", help="data source (none: Configs tab only)")
    ap.add_argument("--config-root", help="directory with the shipped configs (default.yaml, hw_limits.yaml); "
                                          "user copies go in <root>/user. Default: the repo's configs/")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    import uvicorn

    from .app import create_app

    a = parse_args(argv)
    if a.config_root:
        os.environ["B210_SAR_CONFIG_DIR"] = os.path.abspath(a.config_root)  # hw_limits.yaml resolves from here too
    app = create_app(source=a.source, config_root=a.config_root)
    print(f"b210_sar.gui: source={a.source}  open http://{a.host}:{a.port}/", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
