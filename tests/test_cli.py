"""Tests for the CLI wiring (no emulator, no Godot process)."""

from __future__ import annotations

from pathlib import Path

from gen3hd.cli import build_parser, viewer_command


def test_viewer_command_targets_the_project_and_world(tmp_path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    command = viewer_command(world, world / "live.json")

    assert "--path" in command
    assert command[command.index("--path") + 1].endswith("viewer3d")
    assert f"--world={world.resolve()}" in command
    assert f"--live={(world / 'live.json').resolve()}" in command
    # A shot run must not be implied: the viewer stays open for the live feed.
    assert not any(part.startswith("--shot=") for part in command)


def test_viewer_command_forwards_input_when_asked(tmp_path) -> None:
    command = viewer_command(
        tmp_path,
        tmp_path / "live.json",
        input_url="http://127.0.0.1:7331/tool/input",
        session="em-live",
    )

    assert "--input=http://127.0.0.1:7331/tool/input" in command
    assert "--session=em-live" in command


def test_play_is_a_subcommand_with_its_own_defaults() -> None:
    args = build_parser().parse_args(["play", "--rom", "game.gba"])

    assert args.func.__name__ == "_play"
    assert Path(args.out) == Path("out/play")
    assert args.boot is False
    assert args.godot == ""


def test_capture_still_has_flat_switches() -> None:
    args = build_parser().parse_args(["capture", "--rom", "g.gba", "--out", "o"])

    assert args.flat is False
    assert args.no_auto_flat is False
    assert args.func.__name__ == "_capture"
