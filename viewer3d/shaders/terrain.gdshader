shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_disabled, diffuse_burley;

// Palette-true terrain.  The authored GBA tiles are the albedo; a directional
// light supplies real cast shadows and a small emission floor keeps the
// original colour readable inside them.  A baked AO map (ao.png) darkens the
// grass under every card, and low-frequency noise breaks up flat fields
// without inventing new tile art.

uniform sampler2D albedo_tex : source_color, filter_nearest, repeat_disable;
uniform sampler2D ao_tex : filter_linear, repeat_disable;
uniform sampler2D shadow_tex : filter_linear, repeat_disable;
uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform float emission_lift : hint_range(0.0, 1.0) = 0.35;
uniform float ao_strength : hint_range(0.0, 1.0) = 0.55;
uniform float shadow_strength : hint_range(0.0, 1.0) = 0.55;
uniform vec4 shadow_tint : source_color = vec4(0.66, 0.76, 0.96, 1.0);
uniform float variation : hint_range(0.0, 0.2) = 0.045;
uniform float detail_scale = 7.0;
uniform float map_width = 20.0;

varying vec3 world_pos;

float hash21(vec2 p) {
	p = fract(p * vec2(123.34, 456.21));
	p += dot(p, p + 45.32);
	return fract(p.x * p.y);
}

float value_noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	float a = hash21(i);
	float b = hash21(i + vec2(1.0, 0.0));
	float c = hash21(i + vec2(0.0, 1.0));
	float d = hash21(i + vec2(1.0, 1.0));
	return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec4 base = texture(albedo_tex, UV);
	float noise = value_noise(world_pos.xz * detail_scale) * 0.68
		+ value_noise(world_pos.xz * detail_scale * 3.3) * 0.32;
	float ao = texture(ao_tex, UV).a;
	float shadow = texture(shadow_tex, UV).a;
	vec3 col = base.rgb * tint.rgb;
	// A gentle warm-to-cool wash across the map mirrors the side sun and
	// gives the flat plane a readable light gradient.
	float sweep = clamp(world_pos.x / max(map_width, 0.001), 0.0, 1.0);
	col *= mix(vec3(1.035, 1.005, 0.955), vec3(0.955, 0.985, 1.035), sweep);
	col *= 1.0 + (noise - 0.5) * variation;
	col *= 1.0 - ao * ao_strength;
	// The baked silhouette shadow is cool and soft, like skylight.
	col *= mix(vec3(1.0), shadow_tint.rgb, shadow * 0.85);
	col *= 1.0 - shadow * shadow_strength;
	ALBEDO = col;
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;
	EMISSION = col * emission_lift;
}
