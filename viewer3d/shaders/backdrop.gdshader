shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_never, fog_disabled;

// Camera-attached gradient stage.  A deep vertical gradient plus a soft warm
// pool behind the diorama replaces the flat black void without tiling or
// inventing a background field.  Keep it smooth: a hash dither at these very
// low linear values becomes visible patchwork after sRGB encoding.

uniform vec4 top_color = vec4(0.012, 0.016, 0.026, 1.0);
uniform vec4 bottom_color = vec4(0.035, 0.06, 0.075, 1.0);
uniform vec4 glow_color = vec4(0.26, 0.16, 0.08, 1.0);
uniform vec2 glow_center = vec2(0.5, 0.44);
uniform vec2 glow_scale = vec2(1.0, 0.75);
uniform float glow_falloff = 4.0;

void fragment() {
	float t = smoothstep(0.05, 0.95, UV.y);
	vec3 col = mix(bottom_color.rgb, top_color.rgb, t);
	// Rotation-free elliptical glow, strongest just behind the map centre.
	vec2 d = (UV - glow_center) * glow_scale;
	float glow = exp(-dot(d, d) * glow_falloff);
	col += glow_color.rgb * glow;
	ALBEDO = col;
}
