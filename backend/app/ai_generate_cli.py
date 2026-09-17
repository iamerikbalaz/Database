"""Explicit generation and separate exact-packet submission; never called by API."""
import argparse
import getpass
import logging
import os
import stat
import sys
import warnings
from pathlib import Path
from uuid import UUID

from app.ai_generator import (
    MAX_BYTES, GenerationOptions, GeneratorError, generate, load_packet, origin_url,
    submit, validate,
)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo accidental credentials in unsupported argument errors.
        self.exit(2, "Invalid command arguments. See --help; credentials use hidden prompts only.\n")


def hidden(prompt):
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
            raise GeneratorError("HIDDEN_INPUT_UNAVAILABLE") from None


def read_packet(path):
    if Path(path).is_symlink(): raise GeneratorError("INVALID_PACKET_FILE")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise GeneratorError("INVALID_PACKET_FILE")
        return load_packet(handle.read(MAX_BYTES + 1))


def main(argv=None):
    parser = SafeParser(description="Optional OpenAI context-only drafts. No automatic adoption or approval.")
    sub = parser.add_subparsers(dest="action", required=True, parser_class=SafeParser)
    create = sub.add_parser("generate", help="Explicit paid API call; write a new local packet only.")
    send = sub.add_parser("submit", help="Send an existing packet; retry unchanged with the same credential.")
    for command in (create, send):
        command.add_argument("--origin", required=True, help="REAWOTE HTTPS origin, or HTTP loopback origin")
        command.add_argument("--material", required=True, type=UUID)
        command.add_argument("--packet", required=True, help="Private local packet file, outside source assets")
    create.add_argument("--model", required=True, help="Explicit Responses model supporting structured outputs")
    create.add_argument("--reason", required=True)
    create.add_argument("--max-output-tokens", type=int, default=4096)
    args = parser.parse_args(argv)
    try:
        origin = origin_url(args.origin)
        if args.action == "generate":
            options = validate(GenerationOptions, {"model": args.model, "reason": args.reason,
                "max_output_tokens": args.max_output_tokens})
            # Reserve before any network call. Existing files/symlinks are never
            # overwritten; a failed attempt leaves an explicit non-submit-able marker.
            descriptor = os.open(args.packet, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("Generation pending or failed; no valid proposal packet.\n")
                handle.flush(); os.fsync(handle.fileno())
                token = hidden("REAWOTE one-time service credential: ")
                api_key = hidden("OpenAI API key: ")
                packet = generate(origin, args.material, token, api_key, options)
                handle.seek(0); handle.write(packet.model_dump_json(indent=2) + "\n"); handle.truncate()
                handle.flush(); os.fsync(handle.fileno())
            print("Proposal packet saved locally. No proposal has been submitted or approved.")
        else:
            packet = read_packet(args.packet)
            token = hidden("Original REAWOTE service credential: ")
            result = submit(origin, args.material, token, packet)
            print("AI_DRAFT received: " + str(result.id) + ". Human review and approval are still required.")
        return 0
    except GeneratorError as error:
        print("Operation stopped: " + str(error) + ". No automatic retry was made.", file=sys.stderr)
    except (OSError, ValueError):
        print("Local file operation failed. Existing files were not overwritten.", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrupted; inspect the packet and proposal history before retrying.", file=sys.stderr)
    if args.action == "submit":
        print("If the outcome is unknown, retry the unchanged packet with its original credential. "
              "If access expired, inspect proposal history before issuing new access.", file=sys.stderr)
    else:
        print("A provider call may have been billed. Do not automatically repeat generation.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    # Only this standalone process; do not change the application's logging when
    # importing the adapter or invoking main from an embedded test harness.
    logging.disable(logging.CRITICAL)
    raise SystemExit(main())
