shader_type spatial;
render_mode depth_prepass_alpha, cull_disabled;

// Upright alpha-cutout card.  The authored sprite is never re-drawn: we only
// add a warm sun-side rim, a contact darkening at the base and (interactive
// only) a tiny wind sway.  `depth_prepass_alpha` keeps the cutout in the
// directional shadow map.

uniform sampler2D albedo_tex : source_color, filter_nearest, repeat_disable;
uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform bool billboard = true;
uniform float rim_strength : hint_range(0.0, 0.6) = 0.14;
uniform vec4 rim_color : source_color = vec4(1.0, 0.84, 0.58, 1.0);
uniform float base_shade : hint_range(0.0, 0.6) = 0.22;
uniform float sway_amount : hint_range(0.0, 0.2) = 0.0;
uniform vec2 sun_dir = vec2(-0.68, -0.74);
uniform float alpha_cut : hint_range(0.0, 1.0) = 0.5;
uniform bool use_alpha = true;
uniform bool flip_x = false;

void vertex() {
	if (sway_amount > 0.0001) {
		vec3 origin = MODEL_MATRIX[3].xyz;
		float phase = dot(origin, vec3(12.9898, 78.233, 37.719));
		float top = clamp(UV.y, 0.0, 1.0);
		VERTEX.x += sin(TIME * 1.25 + phase) * sway_amount * top;
		VERTEX.z += cos(TIME * 0.95 + phase * 0.71) * sway_amount * 0.35 * top;
	}
	if (billboard) {
		MODELVIEW_MATRIX = VIEW_MATRIX * mat4(
			vec4(length(MODEL_MATRIX[0].xyz), 0.0, 0.0, 0.0),
			vec4(0.0, length(MODEL_MATRIX[1].xyz), 0.0, 0.0),
			vec4(0.0, 0.0, length(MODEL_MATRIX[2].xyz), 0.0),
			vec4(MODEL_MATRIX[3].xyz, 1.0)
		);
	}
}

void fragment() {
	vec2 uv = flip_x ? vec2(1.0 - UV.x, UV.y) : UV;
	vec4 c = texture(albedo_tex, uv);
	if (use_alpha && c.a < alpha_cut) {
		discard;
	}
	vec2 texel = 1.0 / vec2(textureSize(albedo_tex, 0));
	// If the texel just outside the silhouette (toward the sun) is empty,
	// this pixel is on the lit edge; lift it toward the sun colour.  No extra
	// geometry or repainting is involved.
	float outside = texture(albedo_tex, uv + sun_dir * texel * 1.6).a;
	float edge = (use_alpha && outside < alpha_cut) ? 1.0 : 0.0;
	vec3 col = c.rgb * tint.rgb;
	col += rim_color.rgb * edge * rim_strength;
	// The sun is on the left, so the right side of a card sits in shade.
	col *= mix(0.84, 1.04, 1.0 - uv.x);
	// Contact darkening at the card base (UV.y == 0 is the bottom edge).
	float base = 1.0 - smoothstep(0.0, 0.18, uv.y);
	col *= 1.0 - base * base_shade;
	ALBEDO = col;
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;
}
