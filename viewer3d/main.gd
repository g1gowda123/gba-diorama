extends Node3D
## Gen3HD presentation viewer.
##
## Loads a compiled gen3hd world directory and renders it as a lit, atmospheric
## diorama: palette-true terrain, real cast shadows, alpha-cutout cards with a
## sun-side rim, meshed houses, dust motes and a deep stage gradient.
##
## Usage:
##   godot --path viewer3d -- --world=/abs/out/world --shot=/abs/shot.png \
##       --size=1920x1080 --camera=classic --time=golden

const SUN_AZIMUTH := -55.0
const SUN_ELEVATION := 38.0

var world_dir := ""
var shot_path := ""
var render_size := Vector2i(1920, 1080)
var debug_ground := false
var shadows_enabled := true
var dof_enabled := true
var particles_enabled := true
var letterbox := false
var tilt_shift := true
var camera_mode := "classic"
var building_mode := "mesh"
var time_preset := "golden"
var grade_mode := "cinematic"
var dither_enabled := true
var force_dither := false
var sway_enabled := true
var focus_override := Vector2(-1.0, -1.0)
var pitch_override := -1.0
var yaw_override := -1000.0
var distance_override := -1.0
var turntable_frames := 0
var turntable_dir := ""
var turntable_arc := 12.0
var camera_center := Vector3.ZERO
var camera_distance := 0.0
var camera_base_yaw := 0.0
var camera_pitch := 0.0
var debug_shadow := false
var sun_x_override := 1000.0
var sun_y_override := 1000.0
var post_ssao := true
var post_glow := true
var post_fog := true
var test_cards := false
var shadow_strength_override := -1.0
var base_shade_override := -1.0
var cliff_detail := true

var data: Dictionary = {}
var stage: Node3D
var render_viewport: SubViewport
var heights: Array = []
var behaviors: Array = []
var map_w := 0
var map_h := 0
var ground_tex: Texture2D
var shadow_tex: Texture2D
var ao_tex: Texture2D
var ground_shadow_tex: Texture2D
var camera_node: Camera3D
var sun: DirectionalLight3D
var occluders: Array = []
var player_info: Dictionary = {}
var dither_ghost: MeshInstance3D
var shadow_dir := Vector3(0.6, 0.0, 0.8)
var preset: Dictionary = {}
var sway_amount := 0.0
var sway_materials: Array[ShaderMaterial] = []


func _ready() -> void:
	_parse_args()
	if shot_path != "":
		# Stills must be reproducible: no wind phase drift.
		sway_amount = 0.0
	elif turntable_dir != "":
		# The turntable drives a deterministic clock so the loop stays smooth.
		sway_amount = 0.014
	elif sway_enabled:
		sway_amount = 0.018
	if world_dir == "":
		world_dir = ProjectSettings.globalize_path("res://world")
	_apply_window_size()
	_create_stage()
	preset = _preset_for(time_preset)

	var json_path := world_dir.path_join("world.json")
	var text := FileAccess.get_file_as_string(json_path)
	if text == "":
		push_error("cannot read " + json_path)
		get_tree().quit(1)
		return
	data = JSON.parse_string(text)
	if typeof(data) != TYPE_DICTIONARY:
		push_error("invalid world.json: " + json_path)
		get_tree().quit(1)
		return
	map_w = int(data["map"]["width"])
	map_h = int(data["map"]["height"])
	heights = data["grids"]["elevation"]
	behaviors = data["grids"]["behaviors"]
	ground_tex = _load_texture(world_dir.path_join("ground.png"))
	shadow_tex = _load_texture(world_dir.path_join("shadow.png"))
	ao_tex = _load_texture_or_empty(world_dir.path_join("ao.png"))
	ground_shadow_tex = _load_texture_or_empty(world_dir.path_join("shadow_ground.png"))

	_setup_environment()
	_setup_sun()
	_build_terrain()
	_add_island_glow()
	if cliff_detail and camera_mode in ["classic", "map"]:
		_add_cliff_detail()
	if debug_shadow:
		_add_shadow_probe()
	if not debug_ground:
		_build_objects()
		_build_sprites()
	_setup_camera()
	_add_backdrop()
	if particles_enabled:
		_add_motes()
	_create_dither_ghost()
	_update_dither_visibility()
	if tilt_shift and camera_mode == "classic":
		_add_tilt_shift()
	_add_vignette()
	if letterbox:
		_add_letterbox()

	# Let the renderer draw the scene before reading the viewport.
	await get_tree().process_frame
	await get_tree().process_frame
	await get_tree().process_frame
	await RenderingServer.frame_post_draw
	if turntable_frames > 0 and turntable_dir != "":
		await _render_turntable()
	elif shot_path != "":
		var source := render_viewport if render_viewport != null else get_viewport()
		var image := source.get_texture().get_image()
		image.save_png(shot_path)
		print("saved ", shot_path, " ", image.get_width(), "x", image.get_height())
	get_tree().quit()


func _parse_args() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--world="):
			world_dir = arg.trim_prefix("--world=")
		elif arg.begins_with("--shot="):
			shot_path = arg.trim_prefix("--shot=")
		elif arg.begins_with("--size="):
			var parts := arg.trim_prefix("--size=").split("x")
			if parts.size() == 2:
				render_size = Vector2i(int(parts[0]), int(parts[1]))
		elif arg == "--debug-ground":
			debug_ground = true
		elif arg == "--test-shadow":
			debug_shadow = true
		elif arg == "--test-cards":
			test_cards = true
		elif arg == "--no-shadows":
			shadows_enabled = false
		elif arg == "--no-particles":
			particles_enabled = false
		elif arg == "--no-dither":
			dither_enabled = false
		elif arg == "--no-sway":
			sway_enabled = false
		elif arg == "--no-dof":
			dof_enabled = false
		elif arg == "--no-post":
			particles_enabled = false
			dof_enabled = false
			grade_mode = "none"
		elif arg == "--no-ssao":
			post_ssao = false
		elif arg == "--no-glow":
			post_glow = false
		elif arg == "--no-fog":
			post_fog = false
		elif arg == "--force-dither":
			force_dither = true
		elif arg == "--letterbox":
			letterbox = true
		elif arg == "--no-tilt-shift":
			tilt_shift = false
		elif arg == "--no-cliff-detail":
			cliff_detail = false
		elif arg == "--tilt-shift":
			tilt_shift = true
		elif arg == "--dof":
			dof_enabled = true
		elif arg.begins_with("--camera="):
			camera_mode = arg.trim_prefix("--camera=")
		elif arg.begins_with("--buildings="):
			building_mode = arg.trim_prefix("--buildings=")
		elif arg.begins_with("--time="):
			time_preset = arg.trim_prefix("--time=")
		elif arg.begins_with("--grade="):
			grade_mode = arg.trim_prefix("--grade=")
		elif arg.begins_with("--focus="):
			var parts := arg.trim_prefix("--focus=").split(",")
			if parts.size() >= 2:
				focus_override = Vector2(float(parts[0]), float(parts[1]))
		elif arg.begins_with("--pitch="):
			pitch_override = float(arg.trim_prefix("--pitch="))
		elif arg.begins_with("--yaw="):
			yaw_override = float(arg.trim_prefix("--yaw="))
		elif arg.begins_with("--distance="):
			distance_override = float(arg.trim_prefix("--distance="))
		elif arg.begins_with("--turntable="):
			turntable_frames = int(arg.trim_prefix("--turntable="))
		elif arg.begins_with("--turntable-dir="):
			turntable_dir = arg.trim_prefix("--turntable-dir=")
		elif arg.begins_with("--turntable-arc="):
			turntable_arc = float(arg.trim_prefix("--turntable-arc="))
		elif arg.begins_with("--sun-x="):
			sun_x_override = float(arg.trim_prefix("--sun-x="))
		elif arg.begins_with("--sun-y="):
			sun_y_override = float(arg.trim_prefix("--sun-y="))
		elif arg.begins_with("--shadow-strength="):
			shadow_strength_override = float(arg.trim_prefix("--shadow-strength="))
		elif arg.begins_with("--base-shade="):
			base_shade_override = float(arg.trim_prefix("--base-shade="))


func _apply_window_size() -> void:
	if render_size.x <= 0 or render_size.y <= 0:
		return
	var window := get_window()
	# A tiling compositor can force the window smaller; stills render into an
	# offscreen SubViewport at the requested size, so only size the window for
	# interactive use.
	if shot_path == "":
		window.size = render_size


func _create_stage() -> void:
	if shot_path == "" and turntable_dir == "":
		stage = self
		return
	render_viewport = SubViewport.new()
	render_viewport.size = render_size
	render_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	render_viewport.own_world_3d = true
	render_viewport.msaa_3d = Viewport.MSAA_4X
	render_viewport.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
	render_viewport.use_taa = false
	add_child(render_viewport)
	stage = Node3D.new()
	stage.name = "Stage"
	render_viewport.add_child(stage)


func _preset_for(name: String) -> Dictionary:
	var presets := {
		"golden": {
			"bg_top": Color(0.012, 0.030, 0.060),
			"bg_bottom": Color(0.030, 0.075, 0.120),
			"glow": Color(0.100, 0.050, 0.020),
			"glow_center": Vector2(0.25, 0.36),
			"sun_color": Color(1.00, 0.80, 0.56),
			"sun_energy": 1.50,
			"ambient_color": Color(0.30, 0.42, 0.64),
			"ambient_energy": 0.22,
			"emission": 0.20,
			"fog_color": Color(0.055, 0.085, 0.100),
			"fog_density": 0.0035,
			"card_tint": Color(1.015, 0.995, 0.960),
			"mote_color": Color(2.2, 1.7, 0.9, 0.9),
			"mote_amount": 110,
			"exposure": 1.0,
			"shadow_strength": 1.05,
			"vignette": 0.50,
		},
		"day": {
			"bg_top": Color(0.120, 0.220, 0.380),
			"bg_bottom": Color(0.220, 0.380, 0.520),
			"glow": Color(0.150, 0.150, 0.150),
			"glow_center": Vector2(0.50, 0.42),
			"sun_color": Color(1.00, 0.95, 0.86),
			"sun_energy": 1.30,
			"ambient_color": Color(0.52, 0.64, 0.76),
			"ambient_energy": 0.60,
			"emission": 0.30,
			"fog_color": Color(0.115, 0.165, 0.185),
			"fog_density": 0.0025,
			"card_tint": Color(1.0, 1.0, 1.0),
			"mote_color": Color(1.6, 1.7, 1.4, 0.55),
			"mote_amount": 70,
			"exposure": 1.0,
			"shadow_strength": 0.45,
			"vignette": 0.25,
		},
		"dusk": {
			"bg_top": Color(0.016, 0.010, 0.026),
			"bg_bottom": Color(0.070, 0.040, 0.060),
			"glow": Color(0.36, 0.14, 0.09),
			"glow_center": Vector2(0.56, 0.47),
			"sun_color": Color(1.00, 0.60, 0.42),
			"sun_energy": 1.15,
			"ambient_color": Color(0.30, 0.28, 0.52),
			"ambient_energy": 0.26,
			"emission": 0.22,
			"fog_color": Color(0.075, 0.055, 0.095),
			"fog_density": 0.0045,
			"card_tint": Color(1.01, 0.95, 0.97),
			"mote_color": Color(2.4, 1.1, 0.55, 0.9),
			"mote_amount": 120,
			"exposure": 1.0,
			"shadow_strength": 1.0,
			"vignette": 0.55,
		},
		"night": {
			"bg_top": Color(0.004, 0.007, 0.016),
			"bg_bottom": Color(0.020, 0.032, 0.055),
			"glow": Color(0.07, 0.12, 0.22),
			"glow_center": Vector2(0.50, 0.44),
			"sun_color": Color(0.62, 0.74, 1.00),
			"sun_energy": 0.55,
			"ambient_color": Color(0.16, 0.26, 0.50),
			"ambient_energy": 0.34,
			"emission": 0.26,
			"fog_color": Color(0.028, 0.055, 0.090),
			"fog_density": 0.0040,
			"card_tint": Color(0.84, 0.90, 1.03),
			"mote_color": Color(0.9, 2.2, 1.1, 0.9),
			"mote_amount": 150,
			"exposure": 1.0,
			"shadow_strength": 0.95,
			"vignette": 0.60,
		},
	}
	var chosen: Dictionary = presets.get(name, presets["golden"])
	return chosen


func _load_texture(path: String) -> ImageTexture:
	var image := Image.load_from_file(path)
	if image == null:
		push_error("missing texture: " + path)
		return null
	image.convert(Image.FORMAT_RGBA8)
	return ImageTexture.create_from_image(image)


func _load_texture_or_empty(path: String) -> ImageTexture:
	var texture := _load_texture(path)
	if texture != null:
		return texture
	var image := Image.create(2, 2, false, Image.FORMAT_RGBA8)
	image.fill(Color(1, 1, 1, 0))
	return ImageTexture.create_from_image(image)


func _cell_height(x: int, z: int) -> float:
	x = clampi(x, 0, map_w - 1)
	z = clampi(z, 0, map_h - 1)
	var row: Array = heights[z]
	var behavior := int(behaviors[z][x])
	# Ledges are a jump-down *edge*, not a pit.  Gen 3 stores elevation 0 on
	# many of them, which would otherwise become a walled trench.
	if behavior >= 59 and behavior <= 66:
		return 0.10
	# A shallow step keeps the whole map readable from a low camera.
	return (float(row[x]) - 3.0) * 0.08


func _setup_environment() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = preset["bg_top"]
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = preset["ambient_color"]
	env.ambient_light_energy = preset["ambient_energy"]
	if debug_shadow:
		env.ambient_light_energy = 0.0
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	env.tonemap_exposure = preset["exposure"]
	env.glow_enabled = grade_mode != "none" and post_glow
	env.glow_intensity = 0.42
	env.glow_strength = 1.0
	env.glow_bloom = 0.025
	env.glow_blend_mode = Environment.GLOW_BLEND_MODE_ADDITIVE
	env.glow_hdr_threshold = 1.05
	env.glow_hdr_scale = 2.0
	env.fog_enabled = grade_mode != "none" and post_fog
	env.fog_mode = Environment.FOG_MODE_EXPONENTIAL
	env.fog_light_color = preset["fog_color"]
	env.fog_light_energy = 0.7
	env.fog_density = preset["fog_density"]
	env.fog_sky_affect = 0.0
	env.ssao_enabled = grade_mode != "none" and post_ssao
	env.ssao_radius = 2.2
	env.ssao_intensity = 1.8
	env.ssao_power = 1.6
	env.adjustment_enabled = grade_mode == "cinematic"
	env.adjustment_saturation = 1.055
	env.adjustment_contrast = 1.03
	env.adjustment_brightness = 1.0
	var world_env := WorldEnvironment.new()
	world_env.environment = env
	stage.add_child(world_env)


func _setup_sun() -> void:
	sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-SUN_ELEVATION, SUN_AZIMUTH, 0)
	if sun_x_override < 1000.0:
		sun.rotation_degrees.x = sun_x_override
	if sun_y_override < 1000.0:
		sun.rotation_degrees.y = sun_y_override
	sun.light_color = preset["sun_color"]
	sun.light_energy = preset["sun_energy"]
	sun.shadow_enabled = shadows_enabled
	sun.shadow_bias = 0.035
	sun.shadow_normal_bias = 1.6
	sun.shadow_blur = 1.4
	sun.shadow_opacity = 0.9
	sun.directional_shadow_max_distance = 60.0
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
	stage.add_child(sun)
	var basis := Basis.from_euler(Vector3(deg_to_rad(-SUN_ELEVATION), deg_to_rad(SUN_AZIMUTH), 0.0))
	var travel := -basis.z
	shadow_dir = Vector3(travel.x, 0.0, travel.z).normalized()
	if shadow_dir.length() < 0.1:
		shadow_dir = Vector3(0.6, 0.0, 0.8)

	# A cool bounce from the camera side, no shadows, keeps facades readable
	# while the low side-sun carves the long cast shadows.
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-22, 0, 0)
	fill.light_color = Color(0.46, 0.60, 0.95)
	fill.light_energy = 0.30
	fill.shadow_enabled = false
	stage.add_child(fill)


func _build_terrain() -> void:
	var ground := SurfaceTool.new()
	ground.begin(Mesh.PRIMITIVE_TRIANGLES)
	var walls := SurfaceTool.new()
	walls.begin(Mesh.PRIMITIVE_TRIANGLES)

	for z in range(map_h):
		for x in range(map_w):
			var h := _cell_height(x, z)
			var u0 := float(x) / float(map_w)
			var u1 := float(x + 1) / float(map_w)
			var v0 := float(z) / float(map_h)
			var v1 := float(z + 1) / float(map_h)
			_add_quad(
				ground,
				Vector3(x, h, z), Vector3(x, h, z + 1),
				Vector3(x + 1, h, z + 1), Vector3(x + 1, h, z),
				Vector2(u0, v0), Vector2(u0, v1),
				Vector2(u1, v1), Vector2(u1, v0),
				Vector3.UP
			)
			var neighbours := [
				[x - 1, z, -1.0, 0.0], [x + 1, z, 1.0, 0.0],
				[x, z - 1, 0.0, -1.0], [x, z + 1, 0.0, 1.0],
			]
			for item in neighbours:
				var nx: int = item[0]
				var nz: int = item[1]
				var outside := nx < 0 or nz < 0 or nx >= map_w or nz >= map_h
				var nh := -1.85 if outside else _cell_height(nx, nz)
				if h > nh + 0.01:
					_add_wall(walls, x, z, nx, nz, nh, h, outside)

	var ground_mesh := ground.commit()
	var ground_mat := ShaderMaterial.new()
	ground_mat.shader = load("res://shaders/terrain.gdshader")
	ground_mat.set_shader_parameter("albedo_tex", ground_tex)
	ground_mat.set_shader_parameter("ao_tex", ao_tex)
	ground_mat.set_shader_parameter("shadow_tex", ground_shadow_tex)
	ground_mat.set_shader_parameter("tint", preset["card_tint"])
	ground_mat.set_shader_parameter("emission_lift", preset["emission"])
	ground_mat.set_shader_parameter("ao_strength", 0.55)
	ground_mat.set_shader_parameter("shadow_strength", preset["shadow_strength"] if shadow_strength_override < 0.0 else shadow_strength_override)
	ground_mat.set_shader_parameter("shadow_tint", Color(0.42, 0.58, 0.96))
	ground_mat.set_shader_parameter("variation", 0.045)
	ground_mat.set_shader_parameter("map_width", float(map_w))
	if debug_ground:
		ground_mat = ShaderMaterial.new()
		ground_mat.shader = load("res://shaders/terrain.gdshader")
		ground_mat.set_shader_parameter("albedo_tex", ground_tex)
		ground_mat.set_shader_parameter("ao_tex", ao_tex)
		ground_mat.set_shader_parameter("tint", Color(1.0, 0.2, 1.0))
	if debug_shadow:
		var std := StandardMaterial3D.new()
		std.albedo_texture = ground_tex
		std.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
		std.roughness = 1.0
		std.cull_mode = BaseMaterial3D.CULL_DISABLED
		ground_mesh.surface_set_material(0, std)
	else:
		ground_mesh.surface_set_material(0, ground_mat)
	var ground_instance := MeshInstance3D.new()
	ground_instance.mesh = ground_mesh
	ground_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(ground_instance)

	var wall_mesh := walls.commit()
	if wall_mesh != null:
		var wall_mat := ShaderMaterial.new()
		wall_mat.shader = load("res://shaders/wall.gdshader")
		wall_mat.set_shader_parameter("top_color", Color(0.23, 0.165, 0.115))
		wall_mat.set_shader_parameter("bottom_color", Color(0.055, 0.065, 0.080))
		wall_mat.set_shader_parameter("top_y", 0.1)
		wall_mat.set_shader_parameter("bottom_y", -1.85)
		wall_mesh.surface_set_material(0, wall_mat)
		var wall_instance := MeshInstance3D.new()
		wall_instance.mesh = wall_mesh
		wall_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		stage.add_child(wall_instance)


func _add_island_glow() -> void:
	# A soft cool pool under the slab sells the float without tiling the void.
	var quad := QuadMesh.new()
	quad.size = Vector2(map_w * 2.3, map_h * 2.3)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = _soft_dot_texture()
	mat.albedo_color = Color(0.10, 0.22, 0.42, 0.50)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	var glow := MeshInstance3D.new()
	glow.mesh = quad
	glow.material_override = mat
	glow.position = Vector3(map_w * 0.5, -1.72, map_h * 0.5)
	glow.rotation_degrees = Vector3(-90, 0, 0)
	glow.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(glow)


func _add_shadow_probe() -> void:
	var box := BoxMesh.new()
	box.size = Vector3(2.0, 3.0, 2.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.9, 0.1, 0.1)
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	var instance := MeshInstance3D.new()
	instance.mesh = box
	instance.material_override = mat
	instance.position = Vector3(map_w * 0.5, 2.0, map_h * 0.5)
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	stage.add_child(instance)


func _add_quad(
	st: SurfaceTool,
	a: Vector3, b: Vector3, c: Vector3, d: Vector3,
	ua: Vector2, ub: Vector2, uc: Vector2, ud: Vector2,
	normal: Vector3
) -> void:
	st.set_normal(normal)
	st.set_uv(ua); st.add_vertex(a)
	st.set_uv(uc); st.add_vertex(c)
	st.set_uv(ub); st.add_vertex(b)
	st.set_normal(normal)
	st.set_uv(ua); st.add_vertex(a)
	st.set_uv(ud); st.add_vertex(d)
	st.set_uv(uc); st.add_vertex(c)


func _add_wall(st: SurfaceTool, x: int, z: int, nx: int, nz: int, nh: float, h: float, _outside: bool) -> void:
	# Build the wall on the shared edge.  Winding is unimportant: the wall
	# material disables culling.
	var ax := float(x)
	var az := float(z)
	var bx := float(x + 1)
	var bz := float(z + 1)
	var quad: Array[Vector3] = []
	if nx == x - 1:
		quad = [Vector3(ax, nh, bz), Vector3(ax, h, bz), Vector3(ax, h, az), Vector3(ax, nh, az)]
	elif nx == x + 1:
		quad = [Vector3(bx, nh, az), Vector3(bx, h, az), Vector3(bx, h, bz), Vector3(bx, nh, bz)]
	elif nz == z - 1:
		quad = [Vector3(ax, nh, az), Vector3(ax, h, az), Vector3(bx, h, az), Vector3(bx, nh, az)]
	else:
		quad = [Vector3(bx, nh, bz), Vector3(bx, h, bz), Vector3(ax, h, bz), Vector3(ax, nh, bz)]
	var uv := Vector2(0.0, 0.0)
	st.set_normal(Vector3.UP)
	st.set_uv(uv); st.add_vertex(quad[0])
	st.set_uv(uv); st.add_vertex(quad[1])
	st.set_uv(uv); st.add_vertex(quad[2])
	st.set_uv(uv); st.add_vertex(quad[0])
	st.set_uv(uv); st.add_vertex(quad[2])
	st.set_uv(uv); st.add_vertex(quad[3])


func _max_base_height(x0: float, z0: float, x1: float, z1: float) -> float:
	var cx0 := clampi(int(floor(x0)), 0, map_w - 1)
	var cx1 := clampi(int(ceil(x1)), 0, map_w - 1)
	var cz0 := clampi(int(floor(z0)) - 1, 0, map_h - 1)
	var cz1 := clampi(int(ceil(z1)) + 1, 0, map_h - 1)
	var highest := -10.0
	for z in range(cz0, cz1 + 1):
		for x in range(cx0, cx1 + 1):
			highest = max(highest, _cell_height(x, z))
	return highest if highest > -10.0 else 0.0


func _build_objects() -> void:
	var objects: Array = data.get("objects", [])
	for index in range(objects.size()):
		var obj: Dictionary = objects[index]
		var bbox: Array = obj["bbox"]
		var w := float(int(bbox[2]) - int(bbox[0])) / 16.0
		var h := float(int(bbox[3]) - int(bbox[1])) / 16.0
		if w <= 0.0 or h <= 0.0:
			continue
		var texture := _load_texture(world_dir.path_join(String(obj["texture"])))
		if texture == null:
			continue
		var cx := (float(bbox[0]) + float(bbox[2])) * 0.5 / 16.0
		var cz := float(bbox[3]) / 16.0
		# Use the highest ground around the footprint.  A tree on a border
		# cell whose own elevation is 0 would otherwise sink behind the
		# neighbouring raised ground and lose its bottom half.
		var base_y := _max_base_height(
			float(bbox[0]) / 16.0, float(bbox[1]) / 16.0,
			float(bbox[2]) / 16.0, float(bbox[3]) / 16.0
		)
		var kind := String(obj.get("kind", ""))
		if kind == "building" and building_mode == "mesh":
			_add_building(obj, texture, w, h, cx, base_y, cz)
		else:
			var card_pos := Vector3(cx, base_y + h * 0.5 - 0.02, cz)
			var tint := _object_tint(index, kind)
			var hash := (index * 73 + int(cx * 17.0) + int(cz * 29.0)) & 0xFFFF
			var scale := 1.0
			var flip := false
			if kind == "tree":
				scale = 0.95 + float(hash % 13) / 13.0 * 0.11
				flip = hash % 3 == 0
				card_pos.y += (scale - 1.0) * h * 0.5
			_add_billboard(texture, w, h, card_pos, tint, kind == "tree" or kind == "decor", flip, scale)
			occluders.append({"pos": card_pos, "size": Vector2(w * scale, h * scale)})
		# Contact shadow: soft, directional and deliberately lighter than the
		# real shadow map so the two do not double up into mud.  Meshed
		# buildings add their own footprint shadow inside _add_building().
		if not (kind == "building" and building_mode == "mesh"):
			_add_soft_shadow(Vector3(cx, base_y + 0.012, cz), w, h, kind)


func _object_tint(index: int, kind: String) -> Color:
	var jitter := float((index * 37) % 11 - 5) / 100.0
	var tint: Color = preset["card_tint"]
	if kind == "tree":
		return Color(tint.r + jitter * 0.35, tint.g + jitter, tint.b + jitter * 0.2)
	if kind == "decor":
		return Color(tint.r + jitter * 0.8, tint.g + jitter * 0.6, tint.b)
	return tint


func _add_building(obj: Dictionary, texture: Texture2D, w: float, h: float, cx: float, base_y: float, cz: float) -> void:
	var depth := float(obj.get("depth", w * 0.6))
	var back_z := cz - depth
	var mid_z := cz - depth * 0.5
	var eave := clampi(int(obj.get("eave", texture.get_height() / 2)), 1, texture.get_height() - 1)
	var wall_h: float = maxf(0.4, float(texture.get_height() - eave) / 16.0)
	var roof_rise := clampf(float(eave) / 16.0 * 0.55, 0.6, 1.8)
	var roof_style := String(obj.get("roof_style", "flat"))
	var wall_color := _average_color(texture, Rect2i(0, eave, texture.get_width(), texture.get_height() - eave))
	var dark_wall := wall_color * 0.72
	var roof_tint: Color = preset["card_tint"]

	# Walls use only the band below the eave; the roof geometry supplies the
	# top.  This is what stops the facade's baked roof from being stretched
	# across a flat lid.
	var wall_tex := _opaque_region(
		texture, Rect2i(0, eave, texture.get_width(), texture.get_height() - eave)
	)
	var front_mat := _card_material(wall_tex, preset["card_tint"], false)
	_add_face_quad(Vector2(w, wall_h), Vector3(cx, base_y + wall_h * 0.5 - 0.02, cz), Vector3.ZERO, front_mat)

	var back_mat := _flat_material(dark_wall)
	_add_face_quad(Vector2(w, wall_h), Vector3(cx, base_y + wall_h * 0.5 - 0.02, back_z), Vector3.ZERO, back_mat)

	var side_left_tex := _opaque_region(
		texture, Rect2i(0, eave, 16, texture.get_height() - eave)
	)
	var side_right_tex := _opaque_region(
		texture, Rect2i(texture.get_width() - 16, eave, 16, texture.get_height() - eave)
	)
	var side_tint := wall_color * 1.08
	side_tint.a = 1.0
	var side_left_mat := _card_material(side_left_tex, side_tint, false)
	var side_right_mat := _card_material(side_right_tex, side_tint, false)
	_add_face_quad(Vector2(depth, wall_h), Vector3(cx - w * 0.5, base_y + wall_h * 0.5 - 0.02, mid_z), Vector3(0, 90, 0), side_left_mat)
	_add_face_quad(Vector2(depth, wall_h), Vector3(cx + w * 0.5, base_y + wall_h * 0.5 - 0.02, mid_z), Vector3(0, 90, 0), side_right_mat)

	# A darker foundation strip anchors the facade to the grass.
	var foundation_mat := _flat_material(dark_wall * 0.62)
	_add_face_quad(
		Vector2(w, 0.16),
		Vector3(cx, base_y + 0.06, cz + 0.012),
		Vector3.ZERO,
		foundation_mat
	)
	if time_preset != "day":
		_add_door_light(cx, base_y, cz)
		var x_origin := float(obj["bbox"][0])
		for window_box in obj.get("windows", []):
			_add_window_glow(window_box, texture, x_origin, base_y, cz + 0.03)

	var roof_tex := _opaque_region(
		texture, Rect2i(0, 0, texture.get_width(), eave)
	)
	var roof_mat := _card_material(roof_tex, roof_tint, false)
	var wall_top: float = base_y + wall_h - 0.02
	if roof_style == "gable":
		# Ridge runs along X at the middle of the footprint.  A small eave
		# overhang catches the light and stops the roof reading as a sticker.
		var half := depth * 0.5
		var slope := sqrt(half * half + roof_rise * roof_rise)
		var angle := rad_to_deg(atan2(roof_rise, half))
		_add_face_quad(
			Vector2(w + 0.34, slope + 0.18),
			Vector3(cx, wall_top + roof_rise * 0.5, mid_z + half * 0.5),
			Vector3(-angle, 0, 0), roof_mat
		)
		_add_face_quad(
			Vector2(w + 0.34, slope + 0.18),
			Vector3(cx, wall_top + roof_rise * 0.5, mid_z - half * 0.5),
			Vector3(angle, 0, 0), roof_mat
		)
		_add_gable_end(cx - w * 0.5, cz, back_z, mid_z, wall_top, wall_top + roof_rise, side_left_mat)
		_add_gable_end(cx + w * 0.5, cz, back_z, mid_z, wall_top, wall_top + roof_rise, side_right_mat)
		# Ridge beam: a thin warm-lit strip along the top edge.
		var ridge_mat := _flat_material(roof_tint * 1.06)
		_add_face_quad(
			Vector2(w + 0.36, 0.10),
			Vector3(cx, wall_top + roof_rise + 0.02, mid_z),
			Vector3(-90, 0, 0), ridge_mat
		)
	else:
		_add_face_quad(
			Vector2(w + 0.4, depth + 0.4),
			Vector3(cx, wall_top + 0.02, mid_z),
			Vector3(-90, 0, 0), roof_mat
		)
	_add_contact_shadow_quad(Vector3(cx, base_y + 0.014, mid_z), w * 1.45, depth * 1.25, 0.30)


func _add_door_light(cx: float, base_y: float, cz: float) -> void:
	# A warm point light and a tiny additive spill read as light escaping the
	# doorway at dusk.  It is deliberately small: the pixel art still carries
	# the facade.
	var lamp := OmniLight3D.new()
	lamp.position = Vector3(cx, base_y + 0.55, cz + 0.65)
	lamp.light_color = Color(1.0, 0.60, 0.28)
	lamp.light_energy = 2.4
	lamp.omni_range = 4.2
	lamp.omni_attenuation = 1.2
	lamp.shadow_enabled = false
	stage.add_child(lamp)

	# A crisp warm pool on the grass reads as light spilling from the door.
	var pool := QuadMesh.new()
	pool.size = Vector2(2.4, 1.4)
	var pool_mat := StandardMaterial3D.new()
	pool_mat.albedo_texture = _soft_dot_texture()
	pool_mat.albedo_color = Color(2.3, 1.05, 0.42, 0.80)
	pool_mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	pool_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	pool_mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	pool_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	pool_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	pool_mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	var pool_instance := MeshInstance3D.new()
	pool_instance.mesh = pool
	pool_instance.material_override = pool_mat
	pool_instance.position = Vector3(cx, base_y + 0.025, cz + 0.62)
	pool_instance.rotation_degrees = Vector3(-90, 0, 0)
	pool_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(pool_instance)

	var quad := QuadMesh.new()
	quad.size = Vector2(0.42, 0.32)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = _soft_dot_texture()
	mat.albedo_color = Color(2.2, 1.45, 0.65, 0.65)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	mat.billboard_keep_scale = true
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	var glow := MeshInstance3D.new()
	glow.mesh = quad
	glow.material_override = mat
	glow.position = Vector3(cx, base_y + 0.34, cz + 0.04)
	glow.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(glow)

	# A wider, fainter wash tints the facade around the doorway.  The wall
	# material is unshaded, so this additive card is what sells the spill.
	var wash_quad := QuadMesh.new()
	wash_quad.size = Vector2(1.35, 1.0)
	var wash_mat := StandardMaterial3D.new()
	wash_mat.albedo_texture = _soft_dot_texture()
	wash_mat.albedo_color = Color(1.35, 0.68, 0.28, 0.30)
	wash_mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	wash_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	wash_mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	wash_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	wash_mat.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	wash_mat.billboard_keep_scale = true
	wash_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	wash_mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	var wash := MeshInstance3D.new()
	wash.mesh = wash_quad
	wash.material_override = wash_mat
	wash.position = Vector3(cx, base_y + 0.55, cz + 0.035)
	wash.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(wash)


func _add_gable_end(x: float, front_z: float, back_z: float, mid_z: float, wall_top: float, ridge_y: float, material: Material) -> void:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	st.set_normal(Vector3.UP)
	st.set_uv(Vector2(0.0, 0.0)); st.add_vertex(Vector3(x, wall_top, front_z))
	st.set_uv(Vector2(1.0, 0.0)); st.add_vertex(Vector3(x, wall_top, back_z))
	st.set_uv(Vector2(0.5, 1.0)); st.add_vertex(Vector3(x, ridge_y, mid_z))
	var instance := MeshInstance3D.new()
	instance.mesh = st.commit()
	instance.material_override = material
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	stage.add_child(instance)


func _card_material(texture: Texture2D, tint: Color, use_alpha: bool) -> ShaderMaterial:
	var mat := ShaderMaterial.new()
	mat.shader = load("res://shaders/card.gdshader")
	mat.set_shader_parameter("albedo_tex", texture)
	mat.set_shader_parameter("tint", tint)
	mat.set_shader_parameter("billboard", false)
	mat.set_shader_parameter("use_alpha", use_alpha)
	mat.set_shader_parameter("rim_strength", 0.08)
	mat.set_shader_parameter("base_shade", 0.18)
	mat.set_shader_parameter("sway_amount", 0.0)
	return mat


func _opaque_region(texture: Texture2D, region: Rect2i) -> ImageTexture:
	# Building bands include transparent roof corners.  Treating those as
	# holes punches the facade open and shows the terrain behind it, so fill
	# each row's empty pixels with that row's average colour.
	var source := texture.get_image()
	if source == null:
		return ImageTexture.create_from_image(Image.create(2, 2, false, Image.FORMAT_RGBA8))
	source.convert(Image.FORMAT_RGBA8)
	var out := Image.create(region.size.x, region.size.y, false, Image.FORMAT_RGBA8)
	for y in range(region.size.y):
		var total := Color(0, 0, 0, 0)
		var count := 0
		for x in range(region.size.x):
			var c := source.get_pixel(region.position.x + x, region.position.y + y)
			if c.a > 0.5:
				total.r += c.r
				total.g += c.g
				total.b += c.b
				count += 1
		var fill := Color(0.7, 0.65, 0.55)
		if count > 0:
			fill = Color(total.r / count, total.g / count, total.b / count)
		for x in range(region.size.x):
			var c := source.get_pixel(region.position.x + x, region.position.y + y)
			if c.a < 0.5:
				c = Color(fill.r, fill.g, fill.b, 1.0)
			else:
				c.a = 1.0
			out.set_pixel(x, y, c)
	return ImageTexture.create_from_image(out)


func _flat_material(color: Color) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 1.0
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	return mat


func _average_color(texture: Texture2D, region: Rect2i) -> Color:
	var image := texture.get_image()
	if image == null:
		return Color(0.7, 0.65, 0.55)
	image.convert(Image.FORMAT_RGBA8)
	var total := Color(0, 0, 0, 0)
	var count := 0
	var step := maxi(1, region.size.x / 32)
	for y in range(region.position.y, region.position.y + region.size.y, step):
		for x in range(region.position.x, region.position.x + region.size.x, step):
			var c := image.get_pixel(x, y)
			if c.a > 0.5:
				total += c
				count += 1
	if count == 0:
		return Color(0.7, 0.65, 0.55)
	return Color(total.r / count, total.g / count, total.b / count)


func _add_face_quad(size: Vector2, pos: Vector3, rot_degrees: Vector3, material: Material) -> void:
	var quad := QuadMesh.new()
	quad.size = size
	var instance := MeshInstance3D.new()
	instance.mesh = quad
	instance.material_override = material
	instance.position = pos
	instance.rotation_degrees = rot_degrees
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	stage.add_child(instance)


func _build_sprites() -> void:
	var sprites: Array = data.get("sprites", [])
	for spr in sprites:
		var texture := _load_texture(world_dir.path_join(String(spr["texture"])))
		if texture == null:
			continue
		var w := float(texture.get_width()) / 16.0
		var h := float(texture.get_height()) / 16.0
		var world: Array = spr["world"]
		var cx := (float(world[0]) + float(texture.get_width()) * 0.5) / 16.0
		var cz := (float(world[1]) + float(texture.get_height())) / 16.0
		var base_y := _max_base_height(cx - w * 0.5, cz - h, cx + w * 0.5, cz)
		var card_pos := Vector3(cx, base_y + h * 0.5 - 0.02, cz)
		_add_billboard(texture, w, h, card_pos, preset["card_tint"], true, false, 1.0)
		_add_soft_shadow(Vector3(cx, base_y + 0.012, cz - 0.08), w * 0.9, h, "sprite")
		var focus: Array = data.get("camera_focus", [])
		if focus.size() >= 2 and int(world[0]) == int(focus[0]) and int(world[1]) == int(focus[1]):
			player_info = {"pos": card_pos, "size": Vector2(w, h), "texture": texture}


func _add_billboard(texture: Texture2D, w: float, h: float, pos: Vector3, tint: Color, allow_sway: bool, flip: bool, scale: float, cast: bool = true) -> void:
	var quad := QuadMesh.new()
	quad.size = Vector2(w * scale, h * scale)
	var override_mat: Material = null
	if test_cards:
		var std := StandardMaterial3D.new()
		std.albedo_texture = texture
		std.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
		std.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
		std.alpha_scissor_threshold = 0.5
		std.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
		std.billboard_keep_scale = true
		std.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		std.cull_mode = BaseMaterial3D.CULL_DISABLED
		override_mat = std
	else:
		var mat := ShaderMaterial.new()
		mat.shader = load("res://shaders/card.gdshader")
		mat.set_shader_parameter("albedo_tex", texture)
		mat.set_shader_parameter("tint", tint)
		mat.set_shader_parameter("billboard", true)
		mat.set_shader_parameter("rim_strength", 0.24 if h > 1.0 else 0.10)
		mat.set_shader_parameter("rim_color", Color(1.0, 0.80, 0.50))
		mat.set_shader_parameter("base_shade", 0.30 if base_shade_override < 0.0 else base_shade_override)
		mat.set_shader_parameter("sway_amount", sway_amount if allow_sway else 0.0)
		if allow_sway and sway_amount > 0.0 and turntable_dir != "":
			mat.set_shader_parameter("manual_time", true)
			mat.set_shader_parameter("time_value", 0.0)
			sway_materials.append(mat)
		mat.set_shader_parameter("flip_x", flip)
		override_mat = mat
	var instance := MeshInstance3D.new()
	instance.mesh = quad
	instance.material_override = override_mat
	instance.position = pos
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if cast else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(instance)


func _add_soft_shadow(base: Vector3, w: float, h: float, kind: String) -> void:
	if shadow_tex == null or not shadows_enabled:
		return
	var length := clampf(h * 0.45, 0.25, 2.4)
	if kind == "building":
		length = clampf(h * 0.9, 0.5, 3.2)
	var width := w * (1.35 if kind == "tree" else 1.05)
	var center := base + shadow_dir * (length * 0.50)
	_add_contact_shadow_quad(center, width, length, 0.44 if kind != "sprite" else 0.46)


func _add_contact_shadow_quad(center: Vector3, width: float, length: float, opacity: float) -> void:
	if shadow_tex == null or not shadows_enabled:
		return
	var right := Vector3(-shadow_dir.z, 0.0, shadow_dir.x)
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var a := center - right * width * 0.5 - shadow_dir * length * 0.5
	var b := center - right * width * 0.5 + shadow_dir * length * 0.5
	var c := center + right * width * 0.5 + shadow_dir * length * 0.5
	var d := center + right * width * 0.5 - shadow_dir * length * 0.5
	st.set_normal(Vector3.UP)
	st.set_uv(Vector2(0, 0)); st.add_vertex(a)
	st.set_uv(Vector2(0, 1)); st.add_vertex(b)
	st.set_uv(Vector2(1, 1)); st.add_vertex(c)
	st.set_normal(Vector3.UP)
	st.set_uv(Vector2(0, 0)); st.add_vertex(a)
	st.set_uv(Vector2(1, 1)); st.add_vertex(c)
	st.set_uv(Vector2(1, 0)); st.add_vertex(d)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = shadow_tex
	mat.albedo_color = Color(0.02, 0.035, 0.045, opacity)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	var instance := MeshInstance3D.new()
	instance.mesh = st.commit()
	instance.material_override = mat
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(instance)


func _render_turntable() -> void:
	DirAccess.make_dir_recursive_absolute(turntable_dir)
	var arc := deg_to_rad(turntable_arc)
	for index in range(turntable_frames):
		var phase := TAU * float(index) / float(turntable_frames)
		var yaw := camera_base_yaw + arc * 0.5 * sin(phase)
		camera_node.position = camera_center + Vector3(
			sin(yaw) * cos(camera_pitch),
			sin(camera_pitch),
			cos(yaw) * cos(camera_pitch)
		) * camera_distance
		camera_node.look_at(camera_center + Vector3(0.0, -0.20, 0.0), Vector3.UP)
		var sway_clock := TAU / 1.25 * float(index) / float(turntable_frames)
		for material in sway_materials:
			material.set_shader_parameter("time_value", sway_clock)
		await get_tree().process_frame
		await RenderingServer.frame_post_draw
		var source := render_viewport if render_viewport != null else get_viewport()
		var image := source.get_texture().get_image()
		image.save_png(turntable_dir.path_join("frame_%04d.png" % index))
	print("wrote ", turntable_frames, " turntable frames to ", turntable_dir)


func _setup_camera() -> void:
	var center := Vector3(map_w * 0.5, 0.45, map_h * 0.5)
	var yaw := deg_to_rad(16.0)
	var pitch := deg_to_rad(24.0)
	var camera := Camera3D.new()
	camera.projection = Camera3D.PROJECTION_PERSPECTIVE
	camera.fov = 40.0
	if camera_mode == "classic":
		# Whole-map diorama: a slightly raised, fixed perspective on a dark
		# stage.  Trees and houses show their volume without becoming a free
		# 3D camera, and the framing never wanders past the border.
		var classic_yaw := deg_to_rad(0.0 if yaw_override < -999.0 else yaw_override)
		var classic_pitch := deg_to_rad(30.0 if pitch_override < 0.0 else pitch_override)
		var distance: float = maxf(float(map_w), float(map_h)) * 1.62
		if distance_override > 0.0:
			distance = distance_override
		camera.fov = 38.0
		camera.position = center + Vector3(
			sin(classic_yaw) * cos(classic_pitch),
			sin(classic_pitch),
			cos(classic_yaw) * cos(classic_pitch)
		) * distance
		camera_center = center
		camera_distance = distance
		camera_base_yaw = classic_yaw
		camera_pitch = classic_pitch
		stage.add_child(camera)
		camera.look_at(center + Vector3(0.0, -0.20, 0.0), Vector3.UP)
	elif camera_mode == "map":
		camera.projection = Camera3D.PROJECTION_ORTHOGONAL
		camera.size = max(float(map_w), float(map_h)) * 1.32
		camera.position = center + Vector3(
			sin(yaw) * cos(pitch),
			sin(pitch),
			cos(yaw) * cos(pitch)
		) * 30.0
		stage.add_child(camera)
		camera.look_at(center, Vector3.UP)
	elif camera_mode == "play" or camera_mode == "hero":
		# Gameplay framing: a low perspective around the player/scene focus.
		# The map continues past the frame, so no slab edge is visible.
		var focus_px: Array = data.get("camera_focus", [map_w * 8, map_h * 8])
		var fx := float(focus_px[0]) / 16.0
		var fz := float(focus_px[1]) / 16.0
		if camera_mode == "hero":
			fx = float(map_w) * 0.5
			fz = float(map_h) * 0.55
		if focus_override.x >= 0.0:
			fx = focus_override.x
			fz = focus_override.y
		# Keep the camera inside the map even for edge-of-map player spawns.
		fx = clampf(fx, 5.5, float(map_w) - 5.5)
		fz = clampf(fz, 5.0, float(map_h) - 5.0)
		var fy := _cell_height(int(floor(fx)), int(floor(fz)))
		var focus := Vector3(fx, fy + 0.35, fz)
		var play_yaw := deg_to_rad(20.0 if yaw_override < -999.0 else yaw_override)
		var play_pitch := deg_to_rad(23.0 if pitch_override < 0.0 else pitch_override)
		var play_distance := 13.0
		if distance_override > 0.0:
			play_distance = distance_override
		var play_fov := 40.0
		camera.fov = play_fov
		camera.position = focus + Vector3(
			sin(play_yaw) * cos(play_pitch),
			sin(play_pitch),
			cos(play_yaw) * cos(play_pitch)
		) * play_distance
		stage.add_child(camera)
		camera.look_at(focus + Vector3(0.0, 0.55, 0.0), Vector3.UP)
	else:
		stage.add_child(camera)
		camera.position = center + Vector3(0, 10, 10)
		camera.look_at(center, Vector3.UP)
	camera.near = 0.1
	camera.far = 320.0
	var attributes := CameraAttributesPractical.new()
	attributes.dof_blur_far_enabled = dof_enabled and camera_mode != "map"
	attributes.dof_blur_far_distance = 20.0
	attributes.dof_blur_far_transition = 16.0
	attributes.dof_blur_near_enabled = false
	camera.attributes = attributes
	camera.current = true
	camera_node = camera


func _add_backdrop() -> void:
	if camera_node == null:
		return
	var mat := ShaderMaterial.new()
	mat.shader = load("res://shaders/backdrop.gdshader")
	mat.set_shader_parameter("top_color", preset["bg_top"])
	mat.set_shader_parameter("bottom_color", preset["bg_bottom"])
	mat.set_shader_parameter("glow_color", preset["glow"])
	mat.set_shader_parameter("glow_center", preset["glow_center"])
	mat.set_shader_parameter("glow_scale", Vector2(1.60, 1.10))
	mat.set_shader_parameter("glow_falloff", 4.5)
	var distance := 160.0
	var quad := QuadMesh.new()
	var aspect := float(render_size.x) / float(max(1, render_size.y))
	if camera_node.projection == Camera3D.PROJECTION_ORTHOGONAL:
		var span: float = camera_node.size * 1.4
		quad.size = Vector2(span * aspect, span)
	else:
		var half_height := distance * tan(deg_to_rad(camera_node.fov * 0.5))
		quad.size = Vector2(half_height * 2.0 * aspect * 2.5, half_height * 2.0 * 2.5)
	var instance := MeshInstance3D.new()
	instance.mesh = quad
	instance.material_override = mat
	instance.position = Vector3(0.0, 0.0, -distance)
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	camera_node.add_child(instance)


func _add_motes() -> void:
	var motes := GPUParticles3D.new()
	motes.amount = int(preset["mote_amount"])
	motes.lifetime = 14.0
	motes.preprocess = 14.0
	motes.position = Vector3(map_w * 0.5, 1.4, map_h * 0.5)
	motes.visibility_aabb = AABB(
		Vector3(-map_w * 0.7, -3.0, -map_h * 0.7),
		Vector3(map_w * 1.4, 8.0, map_h * 1.4)
	)
	var process := ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = Vector3(map_w * 0.52, 1.6, map_h * 0.52)
	process.direction = Vector3(0.0, 1.0, 0.0)
	process.spread = 180.0
	process.initial_velocity_min = 0.015
	process.initial_velocity_max = 0.07
	process.gravity = Vector3(0.0, 0.012, 0.0)
	process.scale_min = 0.6
	process.scale_max = 1.5
	process.turbulence_enabled = true
	process.turbulence_noise_strength = 0.35
	process.turbulence_noise_scale = 2.5
	process.turbulence_influence_min = 0.02
	process.turbulence_influence_max = 0.10
	motes.process_material = process

	var quad := QuadMesh.new()
	quad.size = Vector2(0.055, 0.055)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = _soft_dot_texture()
	mat.albedo_color = preset["mote_color"]
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	quad.material = mat
	motes.draw_pass_1 = quad
	motes.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(motes)


func _add_cliff_detail() -> void:
	# Hanging vines and protruding rocks break the cut face into a cliff.  It
	# is presentation-only detail: the map itself is untouched.
	var rng := RandomNumberGenerator.new()
	rng.seed = 0x6E33D
	# A grass fringe hangs over the lip so the cut edge is organic.
	var fringe_texture := _grass_fringe_texture(0x51A)
	var fringe_x := 0.0
	while fringe_x < float(map_w):
		var width := 1.0
		var pos := Vector3(fringe_x + width * 0.5, -0.10, float(map_h) + 0.035)
		_add_billboard(fringe_texture, width, 0.34, pos, Color(1.0, 1.0, 1.0), false, false, 1.0, false)
		fringe_x += width * 0.94
	for x in range(0, map_w, 2):
		if rng.randf() < 0.30:
			continue
		var length := 0.75 + rng.randf() * 0.9
		var width := 0.34 + rng.randf() * 0.16
		var texture := _vine_texture(1000 + x * 31 + map_h)
		var pos := Vector3(
			float(x) + 0.35 + rng.randf() * 0.3,
			-length * 0.5 + 0.02,
			float(map_h) + 0.06
		)
		_add_billboard(texture, width, length, pos, Color(0.92, 1.0, 0.88), false, false, 1.0, false)

	var rock_mat := StandardMaterial3D.new()
	rock_mat.albedo_color = Color(0.13, 0.125, 0.115)
	rock_mat.roughness = 1.0
	rock_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	for index in range(7):
		var rock := BoxMesh.new()
		var size := 0.22 + rng.randf() * 0.30
		rock.size = Vector3(size, size * (0.7 + rng.randf() * 0.8), size * 0.8)
		var instance := MeshInstance3D.new()
		instance.mesh = rock
		instance.material_override = rock_mat
		instance.position = Vector3(
			rng.randf() * map_w,
			-0.55 - rng.randf() * 1.05,
			float(map_h) + 0.10
		)
		instance.rotation_degrees = Vector3(
			rng.randf() * 30.0 - 15.0,
			rng.randf() * 50.0,
			rng.randf() * 24.0 - 12.0
		)
		instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		stage.add_child(instance)

	# A few detached shards under the island sell the float.
	for index in range(5):
		var shard := BoxMesh.new()
		var size := 0.12 + rng.randf() * 0.26
		shard.size = Vector3(size, size * 1.2, size)
		var instance := MeshInstance3D.new()
		instance.mesh = shard
		instance.material_override = rock_mat
		instance.position = Vector3(
			rng.randf() * map_w * 1.10 - map_w * 0.05,
			-2.15 - rng.randf() * 1.4,
			rng.randf() * map_h * 1.05
		)
		instance.rotation_degrees = Vector3(rng.randf() * 360.0, rng.randf() * 360.0, rng.randf() * 360.0)
		instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		stage.add_child(instance)


func _vine_texture(seed: int) -> ImageTexture:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed
	var width := 14
	var height := 48
	var image := Image.create(width, height, false, Image.FORMAT_RGBA8)
	var stem := Color(0.11, 0.21, 0.10, 1.0)
	var leaf_a := Color(0.20, 0.40, 0.17, 1.0)
	var leaf_b := Color(0.33, 0.56, 0.24, 1.0)
	var x := 6
	for y in range(height):
		image.set_pixel(x, y, stem)
		image.set_pixel(x + 1, y, stem)
		if y % 4 == 1:
			image.set_pixel(x + 2, y, leaf_a)
		if y % 5 == 2:
			var side := 1 if rng.randf() < 0.5 else -1
			var lx := x + side * (2 + int(rng.randf() * 2.0))
			if lx >= 0 and lx < width:
				image.set_pixel(lx, y, leaf_b if rng.randf() < 0.5 else leaf_a)
				if lx + side >= 0 and lx + side < width:
					image.set_pixel(lx + side, y, leaf_a)
				if lx - side >= 0 and lx - side < width:
					image.set_pixel(lx - side, y, leaf_a)
		if y % 9 == 5 and rng.randf() < 0.6:
			x = clampi(x + (1 if rng.randf() < 0.5 else -1), 3, width - 4)
	return ImageTexture.create_from_image(image)


func _grass_fringe_texture(seed: int) -> ImageTexture:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed
	var width := 32
	var height := 12
	var image := Image.create(width, height, false, Image.FORMAT_RGBA8)
	var light := Color(0.45, 0.78, 0.65, 1.0)
	var mid := Color(0.22, 0.55, 0.40, 1.0)
	var dark := Color(0.12, 0.34, 0.24, 1.0)
	# Solid grass cap, then a broken blade fringe.
	for x in range(width):
		image.set_pixel(x, 0, mid)
		image.set_pixel(x, 1, light if (x * 7 + seed) % 5 else mid)
		var length := 2 + int(rng.randf() * 7.0)
		var shade := dark if rng.randf() < 0.5 else mid
		for y in range(2, 2 + length):
			if y < height:
				image.set_pixel(x, y, shade)
	return ImageTexture.create_from_image(image)


func _add_window_glow(window_box: Array, texture: Texture2D, x_origin: float, base_y: float, front_z: float) -> void:
	if window_box.size() < 4:
		return
	var wx0 := float(window_box[0])
	var wy0 := float(window_box[1])
	var wx1 := float(window_box[2])
	var wy1 := float(window_box[3])
	var centre_x := (x_origin + (wx0 + wx1) * 0.5) / 16.0
	var centre_y := base_y + (float(texture.get_height()) - (wy0 + wy1) * 0.5) / 16.0
	var size := Vector2(
		maxf(0.2, (wx1 - wx0) / 16.0 * 1.25),
		maxf(0.2, (wy1 - wy0) / 16.0 * 1.25)
	)
	var quad := QuadMesh.new()
	quad.size = size
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = _soft_rect_texture()
	mat.albedo_color = Color(2.2, 1.25, 0.45, 0.62)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	mat.billboard_keep_scale = true
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	var glow := MeshInstance3D.new()
	glow.mesh = quad
	glow.material_override = mat
	glow.position = Vector3(centre_x, centre_y, front_z)
	glow.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	stage.add_child(glow)


func _soft_rect_texture() -> ImageTexture:
	var size := 32
	var image := Image.create(size, size, false, Image.FORMAT_RGBA8)
	for y in range(size):
		for x in range(size):
			var edge := minf(
				minf(float(x), float(size - 1 - x)),
				minf(float(y), float(size - 1 - y))
			)
			var alpha := clampf(edge / 3.0, 0.0, 1.0)
			image.set_pixel(x, y, Color(1, 1, 1, alpha))
	return ImageTexture.create_from_image(image)


func _soft_dot_texture() -> ImageTexture:
	var size := 64
	var image := Image.create(size, size, false, Image.FORMAT_RGBA8)
	for y in range(size):
		for x in range(size):
			var dx := (float(x) + 0.5) / float(size) * 2.0 - 1.0
			var dy := (float(y) + 0.5) / float(size) * 2.0 - 1.0
			var d := sqrt(dx * dx + dy * dy)
			var a := clampf(1.0 - d, 0.0, 1.0)
			a = a * a
			image.set_pixel(x, y, Color(1, 1, 1, a))
	return ImageTexture.create_from_image(image)


func _create_dither_ghost() -> void:
	if player_info.is_empty():
		return
	var size: Vector2 = player_info["size"]
	var pos: Vector3 = player_info["pos"]
	var quad := QuadMesh.new()
	quad.size = size
	var mat := ShaderMaterial.new()
	mat.shader = load("res://dither.gdshader")
	mat.set_shader_parameter("albedo_tex", player_info["texture"])
	mat.set_shader_parameter("dither_amount", 0.5)
	var ghost := MeshInstance3D.new()
	ghost.mesh = quad
	ghost.material_override = mat
	ghost.position = pos
	ghost.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	ghost.visible = false
	stage.add_child(ghost)
	dither_ghost = ghost


func _update_dither_visibility() -> void:
	if dither_ghost == null:
		return
	if not dither_enabled:
		dither_ghost.visible = false
		return
	if force_dither:
		dither_ghost.visible = true
		return
	if camera_node == null or player_info.is_empty():
		return
	var pos: Vector3 = player_info["pos"]
	var size: Vector2 = player_info["size"]
	var camera_pos: Vector3 = camera_node.global_position
	var player_distance: float = camera_pos.distance_to(pos)
	var right := camera_node.global_transform.basis.x
	var probe: Array[Vector3] = [
		pos,
		pos + Vector3.UP * size.y * 0.38,
		pos - Vector3.UP * size.y * 0.38,
		pos - Vector3.UP * size.y * 0.5,
	]
	for occluder in occluders:
		var card_pos: Vector3 = occluder["pos"]
		var card_size: Vector2 = occluder["size"]
		if camera_pos.distance_to(card_pos) >= player_distance:
			continue
		var a := camera_node.unproject_position(
			card_pos - right * card_size.x * 0.5 - Vector3.UP * card_size.y * 0.5
		)
		var b := camera_node.unproject_position(
			card_pos + right * card_size.x * 0.5 + Vector3.UP * card_size.y * 0.5
		)
		var rect := Rect2(
			Vector2(minf(a.x, b.x), minf(a.y, b.y)),
			Vector2(absf(b.x - a.x), absf(b.y - a.y))
		).grow(6.0)
		for point in probe:
			var screen_point := camera_node.unproject_position(point)
			if rect.has_point(screen_point):
				dither_ghost.visible = true
				return
	dither_ghost.visible = false


func _process(_delta: float) -> void:
	if dither_enabled:
		_update_dither_visibility()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_T:
		dither_enabled = not dither_enabled
		_update_dither_visibility()
		print("dither ", "on" if dither_enabled else "off")


func _add_tilt_shift() -> void:
	var layer := CanvasLayer.new()
	layer.layer = 9
	var root := Control.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.add_child(root)
	var rect := ColorRect.new()
	rect.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var material := ShaderMaterial.new()
	material.shader = load("res://tilt_shift.gdshader")
	rect.material = material
	root.add_child(rect)
	stage.add_child(layer)


func _add_vignette() -> void:
	var layer := CanvasLayer.new()
	layer.layer = 10
	var root := Control.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.add_child(root)
	var rect := ColorRect.new()
	rect.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var material := ShaderMaterial.new()
	material.shader = load("res://vignette.gdshader")
	material.set_shader_parameter("strength", preset.get("vignette", 0.5))
	rect.material = material
	root.add_child(rect)
	stage.add_child(layer)


func _add_letterbox() -> void:
	var layer := CanvasLayer.new()
	layer.layer = 11
	var root := Control.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.add_child(root)
	for top in [true, false]:
		var bar := ColorRect.new()
		bar.color = Color(0, 0, 0, 1)
		bar.anchor_left = 0.0
		bar.anchor_right = 1.0
		if top:
			bar.anchor_top = 0.0
			bar.anchor_bottom = 0.085
		else:
			bar.anchor_top = 0.915
			bar.anchor_bottom = 1.0
		root.add_child(bar)
	stage.add_child(layer)
