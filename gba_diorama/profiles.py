"""Data-only scene profiles for mapping BG layers to 3D2D roles.

The core renderer must not know that Emerald's BG3 is a backdrop or that
FireRed's BG1 is a canopy.  A profile is a small data record that maps a scene
signature (BG mode, enabled mask, priorities, affine mask, screen sizes) to
layer roles, heights, and materials.  Profiles can be shipped as TOML and
overridden per game or romhack without touching Python code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

VALID_ROLES = {"ground", "world", "foreground", "mid", "top", "ui", "ignore"}


def _parse_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    if value is None:
        return default
    raise TypeError(f"cannot parse integer: {value!r}")


def _int_tuple(value: object, length: int = 4) -> tuple[int, ...]:
    if value is None:
        return (0,) * length
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"expected a list of integers, got {value!r}")
    out = [_parse_int(item) for item in value[:length]]
    out.extend([0] * (length - len(out)))
    return tuple(out)


@dataclass(frozen=True)
class SceneSignature:
    """The PPU state used to choose a profile.

    This deliberately contains no game-specific interpretation.  Two scenes
    with the same signature may still need different profiles, which is why the
    game code is a separate match field.
    """

    bg_mode: int
    enabled_mask: int
    priorities: tuple[int, ...] = (0, 0, 0, 0)
    affine_mask: int = 0
    screen_sizes: tuple[int, ...] = (0, 0, 0, 0)

    @classmethod
    def from_meta(cls, meta: dict[str, Any]) -> "SceneSignature":
        dispcnt = _parse_int(meta.get("dispcnt", 0))
        mode = _parse_int(meta.get("mode", dispcnt & 0x07))
        enabled = (dispcnt >> 8) & 0x0F

        bg_entries = meta.get("bg")
        priorities = [0, 0, 0, 0]
        sizes = [0, 0, 0, 0]
        affine = [False, False, False, False]

        if isinstance(bg_entries, list) and bg_entries:
            for index, entry in enumerate(bg_entries[:4]):
                if not isinstance(entry, dict):
                    continue
                priorities[index] = _parse_int(entry.get("priority", 0))
                sizes[index] = _parse_int(entry.get("size", 0))
                affine[index] = bool(entry.get("affine", False))
                if not enabled and entry.get("enabled", False):
                    enabled |= 1 << index
        else:
            bgcnt = meta.get("bgcnt", [])
            if isinstance(bgcnt, (list, tuple)):
                for index, raw in enumerate(bgcnt[:4]):
                    value = _parse_int(raw)
                    priorities[index] = value & 0x03
                    sizes[index] = (value >> 14) & 0x03
                    affine[index] = bool(value & 0x40)
                if not enabled and bgcnt:
                    enabled = 0x0F

        return cls(
            bg_mode=mode,
            enabled_mask=enabled,
            priorities=tuple(priorities),
            affine_mask=sum(1 << index for index, flag in enumerate(affine) if flag),
            screen_sizes=tuple(sizes),
        )

    def label(self) -> str:
        return (
            f"mode={self.bg_mode} enabled=0b{self.enabled_mask:04b} "
            f"prio={self.priorities} affine=0b{self.affine_mask:04b} "
            f"size={self.screen_sizes}"
        )


@dataclass(frozen=True)
class ProfileMatch:
    mode: int | None = None
    enabled: int | None = None
    priorities: tuple[int, ...] | None = None
    affine_mask: int | None = None
    screen_sizes: tuple[int, ...] | None = None

    def matches(self, signature: SceneSignature) -> bool:
        if self.mode is not None and self.mode != signature.bg_mode:
            return False
        if self.enabled is not None and self.enabled != signature.enabled_mask:
            return False
        if self.priorities is not None and self.priorities != signature.priorities:
            return False
        if self.affine_mask is not None and self.affine_mask != signature.affine_mask:
            return False
        if self.screen_sizes is not None and self.screen_sizes != signature.screen_sizes:
            return False
        return True

    def specificity(self) -> int:
        return sum(
            value is not None
            for value in (
                self.mode,
                self.enabled,
                self.priorities,
                self.affine_mask,
                self.screen_sizes,
            )
        )


@dataclass(frozen=True)
class LayerProfile:
    bg: int
    role: str = "world"
    height: float = 0.0
    material: str = ""
    side_material: str = "stone"
    flat: bool = False
    billboard: bool = False
    min_coverage: float = 0.0
    detector: str = "green"
    min_area: int = 48
    min_alpha: int = 8
    hue_min: float = 90.0
    hue_max: float = 140.0
    saturation_min: float = 0.35
    value_max: float = 0.80

    @property
    def is_ground(self) -> bool:
        return self.role == "ground"

    @property
    def is_ui(self) -> bool:
        return self.flat or self.role in {"ui", "ignore"}


@dataclass(frozen=True)
class CameraProfile:
    """Optional camera overrides carried by a scene profile."""

    pitch_deg: float | None = None
    yaw_deg: float | None = None
    distance: float | None = None


@dataclass(frozen=True)
class SceneProfile:
    name: str
    layers: tuple[LayerProfile, ...]
    match: ProfileMatch = field(default_factory=ProfileMatch)
    game_code: str | None = None
    camera: CameraProfile = field(default_factory=CameraProfile)

    def matches(self, signature: SceneSignature, game_code: str = "") -> bool:
        if self.game_code and self.game_code not in {"*", ""}:
            if not game_code or game_code != self.game_code:
                return False
        return self.match.matches(signature)

    @property
    def ground_layer(self) -> int | None:
        for layer in self.layers:
            if layer.is_ground:
                return layer.bg
        return None

    @property
    def ground_profile(self) -> LayerProfile | None:
        for layer in self.layers:
            if layer.is_ground:
                return layer
        return None

    @property
    def ground_side_material(self) -> str:
        for layer in self.layers:
            if layer.is_ground:
                return layer.side_material or "dirt"
        return "dirt"


@dataclass
class ProfileBook:
    profiles: tuple[SceneProfile, ...] = ()

    @classmethod
    def from_path(cls, path: str | Path) -> "ProfileBook":
        path = Path(path)
        if path.is_dir():
            profiles: list[SceneProfile] = []
            for file in sorted(path.glob("*.toml")):
                profiles.extend(cls.from_path(file).profiles)
            return cls(tuple(profiles))
        return cls.from_text(path.read_text(), source=str(path))

    @classmethod
    def from_text(cls, text: str, source: str = "<string>") -> "ProfileBook":
        if tomllib is None:
            raise RuntimeError(
                "TOML profiles require Python 3.11+ or the optional 'tomli' package"
            )
        data = tomllib.loads(text)
        return cls(tuple(_profiles_from_document(data, source)))

    def match(
        self,
        signature: SceneSignature,
        game_code: str = "",
    ) -> SceneProfile | None:
        candidates = [
            profile for profile in self.profiles if profile.matches(signature, game_code)
        ]
        if not candidates:
            return None
        # Prefer a game-specific profile, then the most constrained signature.
        return max(
            candidates,
            key=lambda profile: (
                bool(profile.game_code and profile.game_code not in {"*", ""}),
                profile.match.specificity(),
            ),
        )


def _layer_from_dict(data: dict[str, Any], source: str) -> LayerProfile:
    if not isinstance(data, dict):
        raise ValueError(f"{source}: each layer must be a table")
    try:
        bg = _parse_int(data["bg"])
    except KeyError as exc:
        raise ValueError(f"{source}: layer is missing 'bg'") from exc
    if not 0 <= bg <= 3:
        raise ValueError(f"{source}: layer bg must be 0..3, got {bg}")
    role = str(data.get("role", "world")).lower()
    if role not in VALID_ROLES:
        raise ValueError(f"{source}: unknown layer role {role!r}")
    detector = str(data.get("detector", "green")).lower()
    if detector not in {"green", "alpha"}:
        raise ValueError(f"{source}: unknown layer detector {detector!r}")
    return LayerProfile(
        bg=bg,
        role=role,
        height=float(data.get("height", 0.0)),
        material=str(data.get("material", "")),
        side_material=str(data.get("side_material", "stone")),
        flat=bool(data.get("flat", False)),
        billboard=bool(data.get("billboard", False)),
        min_coverage=float(data.get("min_coverage", 0.0)),
        detector=detector,
        min_area=_parse_int(data.get("min_area", 48)),
        min_alpha=_parse_int(data.get("min_alpha", 8)),
        hue_min=float(data.get("hue_min", 90.0)),
        hue_max=float(data.get("hue_max", 140.0)),
        saturation_min=float(data.get("saturation_min", 0.35)),
        value_max=float(data.get("value_max", 0.80)),
    )


def _profile_from_dict(
    data: dict[str, Any],
    *,
    name: str,
    source: str,
) -> SceneProfile:
    if not isinstance(data, dict):
        raise ValueError(f"{source}: profile must be a table")
    raw_layers = data.get("layers", [])
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ValueError(f"{source}: profile {name!r} has no layers")
    layers = tuple(_layer_from_dict(layer, source) for layer in raw_layers)

    match_data = data.get("match", {})
    if not isinstance(match_data, dict):
        raise ValueError(f"{source}: profile {name!r} match must be a table")
    priorities = match_data.get("priorities")
    screen_sizes = match_data.get("screen_sizes")
    match = ProfileMatch(
        mode=_optional_int(match_data.get("mode", match_data.get("bg_mode"))),
        enabled=_optional_int(match_data.get("enabled")),
        priorities=_optional_tuple(priorities),
        affine_mask=_optional_int(match_data.get("affine_mask")),
        screen_sizes=_optional_tuple(screen_sizes),
    )

    camera_data = data.get("camera", {})
    if not isinstance(camera_data, dict):
        camera_data = {}
    camera = CameraProfile(
        pitch_deg=_optional_float(camera_data.get("pitch_deg")),
        yaw_deg=_optional_float(camera_data.get("yaw_deg")),
        distance=_optional_float(camera_data.get("distance")),
    )
    return SceneProfile(
        name=name,
        layers=layers,
        match=match,
        game_code=str(data["game_code"]) if data.get("game_code") else None,
        camera=camera,
    )


def _optional_int(value: object) -> int | None:
    return None if value is None else _parse_int(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _optional_tuple(value: object) -> tuple[int, ...] | None:
    return None if value is None else _int_tuple(value)


def _profiles_from_document(data: dict[str, Any], source: str) -> Iterable[SceneProfile]:
    if "profiles" in data:
        entries = data["profiles"]
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            raise ValueError(f"{source}: 'profiles' must be an array of tables")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(f"{source}: profile #{index} must be a table")
            name = str(entry.get("name", f"profile-{index}"))
            yield _profile_from_dict(entry, name=name, source=source)
        return

    if "profile" in data:
        root = data["profile"]
        if isinstance(root, list):
            for index, entry in enumerate(root):
                if not isinstance(entry, dict):
                    raise ValueError(f"{source}: profile #{index} must be a table")
                yield _profile_from_dict(
                    entry,
                    name=str(entry.get("name", f"profile-{index}")),
                    source=source,
                )
            return
        if not isinstance(root, dict):
            raise ValueError(f"{source}: 'profile' must be a table")
        for family, scenes in root.items():
            if not isinstance(scenes, dict):
                continue
            for scene, entry in scenes.items():
                if not isinstance(entry, dict):
                    continue
                name = f"{family}.{scene}"
                yield _profile_from_dict(
                    entry,
                    name=str(entry.get("name", name)),
                    source=source,
                )
        return

    if "layers" in data:
        yield _profile_from_dict(
            data,
            name=str(data.get("name", "profile")),
            source=source,
        )
        return

    raise ValueError(f"{source}: no 'profiles', 'profile', or top-level profile found")


def load_profile_book(path: str | Path) -> ProfileBook:
    return ProfileBook.from_path(path)
