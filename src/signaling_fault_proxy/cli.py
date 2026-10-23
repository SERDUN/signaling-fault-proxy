"""signaling-fault-proxy --upstream https://<core> [--port 8080] [--arm rule,...] [--journal file]

Starts mitmdump as a reverse proxy to the upstream Core with the addon loaded. Any extra arguments
after `--` go to mitmdump as they are.

The Core and the test credentials can come from the environment instead of the command line, so no
secret is typed into a shell or a script:

    SFP_UPSTREAM           the Core to forward to          (same as --upstream)
    SFP_SIGNUP_AS_LOGIN    LOGIN:PASSWORD for the stub      (same as --signup-as-login)
    SFP_CONTROL_TOKEN      a token /__proxy/ calls must carry (same as --control-token)

A command-line flag, when given, wins over the matching variable. The Core and the credentials are
optional: a dashboard at /__proxy/ can set them at runtime, which is how a deployed instance is driven.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

from signaling_fault_proxy import addon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signaling-fault-proxy", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--upstream",
        default=os.environ.get("SFP_UPSTREAM"),
        help="the Core to forward to, e.g. https://core.example.com (or env SFP_UPSTREAM)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="port to listen on (default 8080 / $PORT)",
    )
    parser.add_argument(
        "--control-token",
        default=os.environ.get("SFP_CONTROL_TOKEN", ""),
        help="if set, /__proxy/ control calls must carry ?token=<it> (guards a deployed instance)",
    )
    parser.add_argument(
        "--arm", default="", help="rules to arm at start: name[?param=value&...], comma-separated"
    )
    parser.add_argument("--rules", default="", help="python files with more rules, comma-separated")
    parser.add_argument("--journal", default="", help="file to append every signaling frame to")
    parser.add_argument(
        "--signup-as-login",
        default=os.environ.get("SFP_SIGNUP_AS_LOGIN", ""),
        metavar="LOGIN:PASSWORD",
        help="test stub: serve custom signup (POST /api/v1/user) as a normal login with these creds "
        "(or env SFP_SIGNUP_AS_LOGIN)",
    )
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="arguments for mitmdump after --")
    args = parser.parse_args(argv)

    upstream = args.upstream or ""
    if upstream:
        parsed = urlparse(upstream)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            parser.error("--upstream must be an http(s) URL of the Core")

    # The addon reroutes every request to the configured Core, so the reverse target here is only a
    # placeholder when none is given yet - the dashboard can set the Core at runtime.
    reverse_target = upstream.rstrip("/") if upstream else "http://unset.invalid"
    mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).with_name("mitmdump"))
    extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
    command = [
        mitmdump,
        "--mode",
        f"reverse:{reverse_target}",
        "--listen-host",
        args.host,
        "--listen-port",
        str(args.port),
        "--set",
        "connection_strategy=lazy",
        "--set",
        "flow_detail=0",
        "-s",
        addon.__file__,
        "--set",
        f"arm={args.arm}",
        "--set",
        f"journal={args.journal}",
        "--set",
        f"signup_as_login={args.signup_as_login}",
        "--set",
        f"upstream={upstream}",
        "--set",
        f"control_token={args.control_token}",
        "--set",
        f"rules={','.join(str(Path(p).resolve()) for p in args.rules.split(',') if p)}",
        *extra,
    ]
    print(
        f"listening on http://{args.host}:{args.port} -> {upstream or '(set the Core at /__proxy/)'}",
        file=sys.stderr,
    )
    os.execv(command[0], command)
    return 0  # not reached


if __name__ == "__main__":
    raise SystemExit(main())
