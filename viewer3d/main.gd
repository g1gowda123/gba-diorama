extends Node3D
## Loads a gen3hd world directory and renders it as a low-angle 3D diorama.
## Usage:
##   godot --path viewer3d -- --world=/abs/out/gen3hd-world --shot=/abs/shot.png

var world_dir := ""
var shot_path := ""
var debug_ground := false
var data: Dictionary = {}
var heights: Array = []
var behaviors: Array = []
var map_w := 0
var map_h := 0
var ground_tex: Texture2D
var shadow_tex: Texture2D


func _ready() -> void:
	_parse_args()
	if world_dir == "":
		world_dir = ProjectSettings.globalize_path("res://world")
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

	_setup_environment()
	_build_terrain()
	if not debug_ground:
		_build_objects()
		_build_sprites()
	_setup_camera()
	_add_vignette()

	# Let the renderer draw the scene before reading the viewport.
	await get_tree().process_frame
	await get_tree().process_frame
	await get_tree().process_frame
	await RenderingServer.frame_post_draw
	if shot_path != "":
		var image := get_viewport().get_texture().get_image()
		image.save_png(shot_path)
		print("saved ", shot_path)
	get_tree().quit()


func _parse_args() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--world="):
			world_dir = arg.trim_prefix("--world=")
		elif arg.begins_with("--shot="):
			shot_path = arg.trim_prefix("--shot=")
		elif arg == "--debug-ground":
			debug_ground = true


func _load_texture(path: String) -> ImageTexture:
	var image := Image.load_from_file(path)
	if image == null:
		push_error("missing texture: " + path)
		return null
	image.convert(Image.FORMAT_RGBA8)
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
	env.background_color = Color(0.035, 0.055, 0.09)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.55, 0.65, 0.85)
	env.ambient_light_energy = 0.8
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	env.tonemap_exposure = 1.0
	env.glow_enabled = false
	env.glow_intensity = 0.0
	env.glow_bloom = 0.0
	env.fog_enabled = false
	env.ssao_enabled = false
	env.adjustment_enabled = false
	var sky_material := ProceduralSkyMaterial.new()
	sky_material.sky_top_color = Color(0.04, 0.07, 0.14)
	sky_material.sky_horizon_color = Color(0.22, 0.30, 0.40)
	sky_material.ground_bottom_color = Color(0.02, 0.03, 0.05)
	sky_material.ground_horizon_color = Color(0.12, 0.17, 0.22)
	var sky := Sky.new()
	sky.sky_material = sky_material
	env.sky = sky
	env.background_mode = Environment.BG_SKY
	var world_env := WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-55, -35, 0)
	sun.light_color = Color(1.0, 0.88, 0.72)
	sun.light_energy = 2.0
	sun.shadow_enabled = true
	sun.shadow_bias = 0.04
	sun.shadow_normal_bias = 1.0
	sun.directional_shadow_max_distance = 45.0
	add_child(sun)

	var fill := OmniLight3D.new()
	fill.position = Vector3(map_w * 0.5 - 5.0, 6.0, map_h * 0.5 + 6.0)
	fill.light_color = Color(0.45, 0.62, 1.0)
	fill.light_energy = 2.5
	fill.omni_range = 26.0
	add_child(fill)


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
				var nh := -0.85 if outside else _cell_height(nx, nz)
				if h > nh + 0.01:
					_add_wall(walls, x, z, nx, nz, nh, h, outside)

	var ground_mesh := ground.commit()
	var ground_mat := StandardMaterial3D.new()
	ground_mat.albedo_texture = ground_tex
	ground_mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	ground_mat.roughness = 1.0
	ground_mat.metallic = 0.0
	ground_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	ground_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	if debug_ground:
		ground_mat.albedo_texture = null
		ground_mat.albedo_color = Color(1.0, 0.0, 1.0)
		ground_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	ground_mesh.surface_set_material(0, ground_mat)
	var ground_instance := MeshInstance3D.new()
	ground_instance.mesh = ground_mesh
	add_child(ground_instance)

	var wall_mesh := walls.commit()
	if wall_mesh != null:
		var wall_mat := StandardMaterial3D.new()
		wall_mat.albedo_color = Color(0.20, 0.15, 0.11)
		wall_mat.roughness = 1.0
		wall_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
		wall_mesh.surface_set_material(0, wall_mat)
		var wall_instance := MeshInstance3D.new()
		wall_instance.mesh = wall_mesh
		add_child(wall_instance)

	# A wide dark plane below the map so the diorama sits in a landscape
	# rather than a floating void.  Fog fades its edges.
	var base := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(map_w * 5.0, map_h * 5.0)
	base.mesh = plane
	base.position = Vector3(map_w * 0.5, -0.86, map_h * 0.5)
	var base_mat := StandardMaterial3D.new()
	base_mat.albedo_color = Color(0.34, 0.60, 0.50)
	base_mat.roughness = 1.0
	base_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	base.material_override = base_mat
	add_child(base)


func _add_quad(
	st: SurfaceTool,
	a: Vector3, b: Vector3, c: Vector3, d: Vector3,
	ua: Vector2, ub: Vector2, uc: Vector2, ud: Vector2,
	normal: Vector3
) -> void:
	st.set_normal(normal)
	st.set_uv(ua); st.add_vertex(a)
	st.set_uv(ub); st.add_vertex(b)
	st.set_uv(uc); st.add_vertex(c)
	st.set_normal(normal)
	st.set_uv(ua); st.add_vertex(a)
	st.set_uv(uc); st.add_vertex(c)
	st.set_uv(ud); st.add_vertex(d)


func _add_wall(st: SurfaceTool, x: int, z: int, nx: int, nz: int, nh: float, h: float, outside: bool) -> void:
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
	for obj in objects:
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
		_add_billboard(texture, w, h, Vector3(cx, base_y + h * 0.5 - 0.02, cz), 1.0)
		_add_contact_shadow(Vector3(cx, base_y + 0.02, cz - 0.12), w * 0.95, w * 0.55)


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
		_add_billboard(texture, w, h, Vector3(cx, base_y + h * 0.5 - 0.02, cz), 1.0)
		_add_contact_shadow(Vector3(cx, base_y + 0.02, cz - 0.08), w * 0.8, w * 0.42)


func _add_billboard(texture: Texture2D, w: float, h: float, pos: Vector3, alpha_cut: float) -> void:
	var quad := QuadMesh.new()
	quad.size = Vector2(w, h)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = texture
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
	mat.alpha_scissor_threshold = alpha_cut
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	mat.billboard_keep_scale = true
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.roughness = 1.0
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	var instance := MeshInstance3D.new()
	instance.mesh = quad
	instance.material_override = mat
	instance.position = pos
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	add_child(instance)


func _add_contact_shadow(pos: Vector3, w: float, h: float) -> void:
	if shadow_tex == null:
		return
	var quad := QuadMesh.new()
	quad.size = Vector2(w, h)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = shadow_tex
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	var instance := MeshInstance3D.new()
	instance.mesh = quad
	instance.material_override = mat
	instance.position = pos
	instance.rotation_degrees = Vector3(-90, 0, 0)
	add_child(instance)


func _setup_camera() -> void:
	var center := Vector3(map_w * 0.5, 0.35, map_h * 0.5)
	var yaw := deg_to_rad(16.0)
	var pitch := deg_to_rad(24.0)
	var camera := Camera3D.new()
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = max(float(map_w), float(map_h)) * 1.35
	camera.position = center + Vector3(
		sin(yaw) * cos(pitch),
		sin(pitch),
		cos(yaw) * cos(pitch)
	) * 30.0
	add_child(camera)
	camera.look_at(center, Vector3.UP)
	camera.near = 0.1
	camera.far = 240.0
	var attributes := CameraAttributesPractical.new()
	attributes.dof_blur_far_enabled = false
	attributes.dof_blur_near_enabled = false
	camera.attributes = attributes
	camera.current = true


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
	rect.material = material
	root.add_child(rect)
	add_child(layer)
