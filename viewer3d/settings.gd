class_name ViewerSettings
extends RefCounted

## One place for every presentation switch.
##
## `viewer3d/settings.json` supplies the defaults, so a user can disable a
## feature (shadows, contact shadows, lanterns, particles, cliff detail) by
## editing one file instead of remembering flags.  Command-line flags are
## applied *after* the file, so a one-off render can still override it:
##
##   godot --path viewer3d -- --world=... --no-shadows
##
## Unknown keys are ignored; a missing key falls back to the default below, so
## an older settings file keeps working after a new switch is added.

const DEFAULTS := {
	"camera": {
		"mode": "classic",  # classic | hero | play | map
	},
	"time": "golden",  # golden | day | dusk | night
	"grade": "cinematic",  # cinematic | none
	"buildings": "mesh",  # mesh | card
	"render": {
		"dof": true,
		"tilt_shift": true,
		"vignette": true,
		"bloom": true,
		"ssao": true,
		"fog": true,
		"particles": true,
		"dither": true,
		"sway": true,
		"letterbox": false,
	},
	"shadows": {
		"cast": true,      # real-time directional shadow map
		"contact": true,   # soft quads under cards and sprites
		"baked": true,     # pre-baked ground shadow overlay
		"strength": -1.0,  # <0 keeps the time preset's value
	},
	"island": {
		"cliff_detail": true,  # fringe, vines, rocks together
		"fringe": true,
		"vines": true,
		"rocks": true,
		"shards": true,
		"rim_lip": true,
		"glow": true,
	},
	"lights": {
		"celestial": true,  # sun/moon disc
		"door": true,       # warm doorway pool
		"windows": true,    # lit windows at dusk/night
		"lanterns": true,   # lantern fixture beside the door
	},
	"live": {
		"path": "",
		"follow": false,
	},
}


static func load_file(path: String) -> Dictionary:
	"""Deep-merge `path` over DEFAULTS. A missing or bad file yields defaults."""
	var merged: Dictionary = DEFAULTS.duplicate(true)
	if path == "" or not FileAccess.file_exists(path):
		return merged
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("settings: %s is not a JSON object; using defaults" % path)
		return merged
	_merge(merged, parsed)
	return merged


static func value(settings: Dictionary, path: String, fallback = null):
	"""Read a dotted path ("shadows.contact") with a default for a gap."""
	var current = settings
	for part in path.split("."):
		if not (current is Dictionary) or not current.has(part):
			return fallback
		current = current[part]
	return current


static func _merge(base: Dictionary, overlay: Dictionary) -> void:
	for key in overlay.keys():
		var value = overlay[key]
		if value is Dictionary and base.get(key) is Dictionary:
			_merge(base[key], value)
		else:
			base[key] = value
